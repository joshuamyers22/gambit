"""Separate persistence format for forecast-combination evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from gambit.forecasting import ForecastCombinationResult

FORECAST_COMBINATION_FORMAT = "gambit.forecast-combination"
FORECAST_COMBINATION_VERSION = 1
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_TABLE_BYTES = 512 * 1024 * 1024

_FORECAST_COLUMNS = ["timestamp", "symbol", "raw_forecast", "available_rule_count"]
_CONTRIBUTION_COLUMNS = [
    "timestamp",
    "symbol",
    "rule",
    "weight",
    "raw_forecast",
    "available",
    "scalar",
    "scaled_forecast",
    "capped_forecast",
    "effective_weight",
    "diversification_multiplier",
    "contribution",
]


class ForecastCombinationResultError(ValueError):
    """A persisted forecast-combination result is incomplete or inconsistent."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ForecastCombinationResultError(f"duplicate manifest field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ForecastCombinationResultError(f"invalid JSON number: {value}")


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ForecastCombinationResultError(f"{name} must be an object")
    return value


def _bounded_read(path: Path, maximum: int) -> bytes:
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise ForecastCombinationResultError(f"forecast artifact file exceeds {maximum} bytes: {path.name}")
    return data


def _validate_forecasts(frame: pl.DataFrame) -> None:
    expected_schema = {
        "timestamp": pl.Datetime("ns"),
        "symbol": pl.String,
        "raw_forecast": pl.Float64,
        "available_rule_count": pl.UInt32,
    }
    if frame.columns != _FORECAST_COLUMNS or frame.schema != expected_schema:
        raise ForecastCombinationResultError("forecast table schema mismatch")
    if frame.is_empty():
        raise ForecastCombinationResultError("forecast table cannot be empty")
    if frame.null_count().sum_horizontal().item():
        raise ForecastCombinationResultError("forecast table cannot contain null values")
    if not bool(frame["raw_forecast"].is_finite().all()):
        raise ForecastCombinationResultError("combined forecasts must be finite")
    if frame.select((pl.col("symbol").str.len_chars() == 0).any()).item():
        raise ForecastCombinationResultError("forecast symbols cannot be empty")
    if frame.select(pl.struct(["timestamp", "symbol"]).is_duplicated().any()).item():
        raise ForecastCombinationResultError("forecast table requires one row per timestamp and symbol")
    if not frame.equals(frame.sort("timestamp", "symbol")):
        raise ForecastCombinationResultError("forecast table must be sorted by timestamp and symbol")


def _validate_contributions(frame: pl.DataFrame) -> None:
    expected_schema = {
        "timestamp": pl.Datetime("ns"),
        "symbol": pl.String,
        "rule": pl.String,
        "weight": pl.Float64,
        "raw_forecast": pl.Float64,
        "available": pl.Boolean,
        "scalar": pl.Float64,
        "scaled_forecast": pl.Float64,
        "capped_forecast": pl.Float64,
        "effective_weight": pl.Float64,
        "diversification_multiplier": pl.Float64,
        "contribution": pl.Float64,
    }
    if frame.columns != _CONTRIBUTION_COLUMNS or frame.schema != expected_schema:
        raise ForecastCombinationResultError("forecast contribution table schema mismatch")
    if frame.is_empty():
        raise ForecastCombinationResultError("forecast contribution table cannot be empty")
    required = frame.select(
        "timestamp",
        "symbol",
        "rule",
        "weight",
        "available",
        "scalar",
        "effective_weight",
        "diversification_multiplier",
        "contribution",
    )
    if required.null_count().sum_horizontal().item():
        raise ForecastCombinationResultError("required forecast contribution values cannot be null")
    if frame.select(
        ((pl.col("symbol").str.len_chars() == 0) | (pl.col("rule").str.len_chars() == 0)).any()
    ).item():
        raise ForecastCombinationResultError("forecast contribution symbols and rules cannot be empty")
    if frame.select(pl.struct(["timestamp", "symbol", "rule"]).is_duplicated().any()).item():
        raise ForecastCombinationResultError("forecast contributions require one row per timestamp, symbol, and rule")
    if not frame.equals(frame.sort("timestamp", "symbol", "rule")):
        raise ForecastCombinationResultError("forecast contributions must be sorted by timestamp, symbol, and rule")
    if frame.select(
        pl.any_horizontal(
            pl.col("weight", "scalar", "effective_weight", "diversification_multiplier", "contribution").is_nan()
            | pl.col("weight", "scalar", "effective_weight", "diversification_multiplier", "contribution").is_infinite()
        ).any()
    ).item():
        raise ForecastCombinationResultError("forecast contribution values must be finite")
    if frame.select(
        (
            (pl.col("weight") < 0)
            | (pl.col("scalar") <= 0)
            | (pl.col("effective_weight") < 0)
            | (pl.col("diversification_multiplier") < 1)
        ).any()
    ).item():
        raise ForecastCombinationResultError("forecast contribution scales and weights are invalid")
    grouped_weights = frame.group_by("timestamp", "symbol", maintain_order=True).agg(
        pl.col("weight").sum().alias("base_weight"),
        pl.col("weight").filter(pl.col("available")).sum().alias("available_weight"),
        pl.col("effective_weight").sum().alias("effective_weight"),
    )
    if not np.allclose(grouped_weights["base_weight"].to_numpy(), 1.0, rtol=0.0, atol=1e-12):
        raise ForecastCombinationResultError("forecast base weights must sum to one in every group")
    zero_policy = np.isclose(
        grouped_weights["effective_weight"].to_numpy(),
        grouped_weights["available_weight"].to_numpy(),
        rtol=0.0,
        atol=1e-12,
    )
    renormalized_policy = np.isclose(
        grouped_weights["effective_weight"].to_numpy(),
        1.0,
        rtol=0.0,
        atol=1e-12,
    )
    empty_policy = np.isclose(
        grouped_weights["available_weight"].to_numpy(),
        0.0,
        rtol=0.0,
        atol=1e-12,
    ) & np.isclose(
        grouped_weights["effective_weight"].to_numpy(),
        0.0,
        rtol=0.0,
        atol=1e-12,
    )
    if not bool(np.all(zero_policy | renormalized_policy | empty_policy)):
        raise ForecastCombinationResultError("forecast effective weights do not match a declared missing policy")
    if frame.select((~pl.col("available") & (pl.col("effective_weight") != 0)).any()).item():
        raise ForecastCombinationResultError("unavailable rules must have zero effective weight")
    for column in ("raw_forecast", "scaled_forecast", "capped_forecast"):
        if frame.select((pl.col(column).is_null() != ~pl.col("available")).any()).item():
            raise ForecastCombinationResultError("forecast contribution availability is inconsistent")
        if frame.select(pl.col(column).is_not_null().and_(~pl.col(column).is_finite()).any()).item():
            raise ForecastCombinationResultError("available forecast contribution values must be finite")
    expected = np.where(
        frame["available"].to_numpy(),
        frame["capped_forecast"].fill_null(0.0).to_numpy()
        * frame["effective_weight"].to_numpy()
        * frame["diversification_multiplier"].to_numpy(),
        0.0,
    )
    if not np.allclose(frame["contribution"].to_numpy(), expected, rtol=1e-12, atol=1e-12):
        raise ForecastCombinationResultError("forecast contribution calculation is inconsistent")


def _validate_result(result: ForecastCombinationResult) -> tuple[pl.DataFrame, pl.DataFrame]:
    if not isinstance(result, ForecastCombinationResult):
        raise TypeError("result must be ForecastCombinationResult")
    forecasts = result.forecasts
    contributions = result.contributions
    _validate_forecasts(forecasts)
    _validate_contributions(contributions)
    reconciled = (
        contributions.group_by("timestamp", "symbol", maintain_order=True)
        .agg(
            pl.col("contribution").sum().alias("raw_forecast"),
            pl.col("available").sum().cast(pl.UInt32).alias("available_rule_count"),
        )
        .sort("timestamp", "symbol")
    )
    if not forecasts.equals(reconciled):
        raise ForecastCombinationResultError("combined forecasts do not reconcile to rule contributions")
    return forecasts, contributions


def _write_table(frame: pl.DataFrame, path: Path) -> bytes:
    frame.write_ipc(path, compression="uncompressed")
    data = path.read_bytes()
    with path.open("rb") as persisted:
        os.fsync(persisted.fileno())
    return data


def save_forecast_combination_result(result: ForecastCombinationResult, destination: str | Path) -> Path:
    """Atomically save reconciled combined forecasts and contribution evidence."""
    forecasts, contributions = _validate_result(result)
    destination_path = Path(destination)
    if destination_path.exists():
        raise FileExistsError(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = Path(tempfile.mkdtemp(prefix=f".{destination_path.name}.", dir=destination_path.parent))
    try:
        forecast_data = _write_table(forecasts, temporary_path / "forecasts.arrow")
        contribution_data = _write_table(contributions, temporary_path / "contributions.arrow")
        manifest = {
            "format": FORECAST_COMBINATION_FORMAT,
            "version": FORECAST_COMBINATION_VERSION,
            "tables": {
                "forecasts": {
                    "file": "forecasts.arrow",
                    "sha256": _digest(forecast_data),
                    "rows": forecasts.height,
                },
                "contributions": {
                    "file": "contributions.arrow",
                    "sha256": _digest(contribution_data),
                    "rows": contributions.height,
                },
            },
        }
        manifest_path = temporary_path / "manifest.json"
        manifest_path.write_bytes(_canonical_json(manifest) + b"\n")
        with manifest_path.open("rb") as persisted_manifest:
            os.fsync(persisted_manifest.fileno())
        directory_fd = os.open(temporary_path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.replace(temporary_path, destination_path)
        parent_fd = os.open(destination_path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except BaseException:
        if temporary_path.exists():
            for child in temporary_path.iterdir():
                child.unlink()
            temporary_path.rmdir()
        raise
    return destination_path


def _load_table(source: Path, metadata: Any, *, name: str, filename: str) -> pl.DataFrame:
    payload = _object(metadata, f"{name} metadata")
    if set(payload) != {"file", "sha256", "rows"} or payload.get("file") != filename:
        raise ForecastCombinationResultError(f"forecast {name} metadata mismatch")
    data = _bounded_read(source / filename, _MAX_TABLE_BYTES)
    if not isinstance(payload["sha256"], str) or _digest(data) != payload["sha256"]:
        raise ForecastCombinationResultError(f"forecast {name} checksum mismatch")
    frame = pl.read_ipc(data, memory_map=False)
    if type(payload["rows"]) is not int or payload["rows"] < 0 or frame.height != payload["rows"]:
        raise ForecastCombinationResultError(f"forecast {name} row count mismatch")
    return frame


def load_forecast_combination_result(source: str | Path) -> ForecastCombinationResult:
    """Load version-one forecast evidence after checksum and reconciliation checks."""
    source_path = Path(source)
    try:
        manifest = _object(
            json.loads(
                _bounded_read(source_path / "manifest.json", _MAX_MANIFEST_BYTES),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
            ),
            "manifest",
        )
        if set(manifest) != {"format", "version", "tables"}:
            raise ForecastCombinationResultError("forecast manifest has unexpected fields")
        if (
            manifest.get("format") != FORECAST_COMBINATION_FORMAT
            or type(manifest.get("version")) is not int
            or manifest["version"] != FORECAST_COMBINATION_VERSION
        ):
            raise ForecastCombinationResultError("unsupported forecast-combination result format or version")
        tables = _object(manifest["tables"], "tables")
        if set(tables) != {"forecasts", "contributions"}:
            raise ForecastCombinationResultError("forecast manifest tables are incomplete")
        forecasts = _load_table(source_path, tables["forecasts"], name="forecasts", filename="forecasts.arrow")
        contributions = _load_table(
            source_path,
            tables["contributions"],
            name="contributions",
            filename="contributions.arrow",
        )
        result = ForecastCombinationResult(forecasts, contributions)
        _validate_result(result)
        return result
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        OverflowError,
        json.JSONDecodeError,
        pl.exceptions.PolarsError,
    ) as error:
        if isinstance(error, ForecastCombinationResultError):
            raise
        raise ForecastCombinationResultError(f"invalid forecast-combination result: {source_path}") from error
