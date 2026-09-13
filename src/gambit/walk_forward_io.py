"""Separate persistence format for optimized walk-forward evidence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import polars as pl

from gambit.optimize import (
    WalkForwardExperimentResult,
    WalkForwardFold,
    WalkForwardInterval,
    WalkForwardOptimizationFoldResult,
    WalkForwardTrialFailure,
    WalkForwardTrialResult,
)

WALK_FORWARD_FORMAT = "gambit.walk-forward-experiment"
WALK_FORWARD_VERSION = 1
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_EQUITY_BYTES = 512 * 1024 * 1024


class WalkForwardResultError(ValueError):
    """A persisted walk-forward experiment is incomplete or inconsistent."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WalkForwardResultError(f"duplicate manifest field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise WalkForwardResultError(f"invalid JSON number: {value}")


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WalkForwardResultError(f"{name} must be an object")
    return value


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise WalkForwardResultError(f"{name} must be an array")
    return value


def _bounded_read(path: Path, maximum: int) -> bytes:
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise WalkForwardResultError(f"walk-forward artifact file exceeds {maximum} bytes: {path.name}")
    return data


def _interval(value: Any, name: str) -> WalkForwardInterval:
    payload = _object(value, name)
    if set(payload) != {"start", "stop"}:
        raise WalkForwardResultError(f"{name} has unexpected fields")
    return WalkForwardInterval(payload["start"], payload["stop"])


def _fold_payload(result: WalkForwardOptimizationFoldResult, equity_start: int) -> dict[str, Any]:
    fold = result.fold
    return {
        "index": fold.index,
        "split_id": fold.split_id,
        "intervals": {
            "warmup": {"start": fold.warmup.start, "stop": fold.warmup.stop},
            "fit": {"start": fold.fit.start, "stop": fold.fit.stop},
            "validation": {"start": fold.validation.start, "stop": fold.validation.stop},
            "heldout": {"start": fold.heldout.start, "stop": fold.heldout.stop},
        },
        "seed": result.seed,
        "selected_parameters": dict(result.selected_parameters),
        "validation_cost": result.validation_cost,
        "validation_metrics": dict(result.validation_metrics),
        "heldout_metrics": dict(result.heldout_metrics),
        "input_sha256": result.input_sha256,
        "model_sha256": result.model_sha256,
        "equity_start": equity_start,
        "equity_stop": equity_start + result.heldout_equity.height,
        "trials": [
            {
                "parameters": dict(trial.parameters),
                "validation_cost": trial.validation_cost,
                "validation_metrics": dict(trial.validation_metrics),
            }
            for trial in result.trials
        ],
        "failures": [
            {
                "parameters": dict(failure.parameters),
                "error_type": failure.error_type,
                "message": failure.message,
            }
            for failure in result.failures
        ],
    }


def save_walk_forward_result(result: WalkForwardExperimentResult, destination: str | Path) -> Path:
    """Atomically save metadata plus a checksummed Arrow equity table."""
    if not isinstance(result, WalkForwardExperimentResult):
        raise TypeError("result must be WalkForwardExperimentResult")
    destination_path = Path(destination)
    if destination_path.exists():
        raise FileExistsError(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = Path(tempfile.mkdtemp(prefix=f".{destination_path.name}.", dir=destination_path.parent))
    try:
        equity = result.out_of_sample_equity
        equity_path = temporary_path / "equity.arrow"
        equity.write_ipc(equity_path, compression="uncompressed")
        equity_data = equity_path.read_bytes()
        with equity_path.open("rb") as persisted_equity:
            os.fsync(persisted_equity.fileno())
        folds: list[dict[str, Any]] = []
        equity_start = 0
        for fold in result:
            folds.append(_fold_payload(fold, equity_start))
            equity_start += fold.heldout_equity.height
        manifest = {
            "format": WALK_FORWARD_FORMAT,
            "version": WALK_FORWARD_VERSION,
            "input_sha256": result.input_sha256,
            "equity": {
                "file": "equity.arrow",
                "sha256": _digest(equity_data),
                "rows": equity.height,
                "timestamp_column": equity.columns[0],
            },
            "folds": folds,
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


def _trial(value: Any) -> WalkForwardTrialResult:
    payload = _object(value, "trial")
    return WalkForwardTrialResult(
        parameters=_object(payload["parameters"], "trial parameters"),
        validation_cost=payload["validation_cost"],
        validation_metrics=_object(payload["validation_metrics"], "trial validation metrics"),
    )


def _failure(value: Any) -> WalkForwardTrialFailure:
    payload = _object(value, "failure")
    return WalkForwardTrialFailure(
        parameters=_object(payload["parameters"], "failure parameters"),
        error_type=payload["error_type"],
        message=payload["message"],
    )


def load_walk_forward_result(source: str | Path) -> WalkForwardExperimentResult:
    """Load a version-one walk-forward artifact after identity checks."""
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
        if (
            manifest.get("format") != WALK_FORWARD_FORMAT
            or type(manifest.get("version")) is not int
            or manifest["version"] != WALK_FORWARD_VERSION
        ):
            raise WalkForwardResultError("unsupported walk-forward result format or version")
        equity_metadata = _object(manifest["equity"], "equity metadata")
        if equity_metadata.get("file") != "equity.arrow":
            raise WalkForwardResultError("walk-forward equity file must be equity.arrow")
        equity_data = _bounded_read(source_path / "equity.arrow", _MAX_EQUITY_BYTES)
        if _digest(equity_data) != equity_metadata["sha256"]:
            raise WalkForwardResultError("walk-forward equity checksum mismatch")
        equity = pl.read_ipc(equity_data, memory_map=False)
        timestamp_column = equity_metadata["timestamp_column"]
        if not isinstance(timestamp_column, str) or equity.columns != [timestamp_column, "equity"]:
            raise WalkForwardResultError("walk-forward equity schema mismatch")
        if equity.schema[timestamp_column] != pl.Datetime("ns") or equity.schema["equity"] != pl.Float64:
            raise WalkForwardResultError("walk-forward equity types must be Datetime(ns) and Float64")
        if type(equity_metadata["rows"]) is not int or equity.height != equity_metadata["rows"]:
            raise WalkForwardResultError("walk-forward equity row count mismatch")
        if equity.null_count().sum_horizontal().item() or not bool(equity["equity"].is_finite().all()):
            raise WalkForwardResultError("walk-forward equity cannot contain null or non-finite values")

        input_sha256 = manifest["input_sha256"]
        folds: list[WalkForwardOptimizationFoldResult] = []
        equity_cursor = 0
        for expected_index, value in enumerate(_array(manifest["folds"], "folds")):
            payload = _object(value, "fold")
            intervals = _object(payload["intervals"], "fold intervals")
            if payload["index"] != expected_index:
                raise WalkForwardResultError("walk-forward folds must be complete and ordered")
            split_id = payload["split_id"]
            if not isinstance(split_id, str) or len(split_id) != 64:
                raise WalkForwardResultError("walk-forward split identity must be a SHA-256 digest")
            start, stop = payload["equity_start"], payload["equity_stop"]
            if type(start) is not int or type(stop) is not int or start != equity_cursor or stop < start:
                raise WalkForwardResultError("walk-forward equity fold offsets are inconsistent")
            fold = WalkForwardFold(
                index=expected_index,
                warmup=_interval(intervals["warmup"], "warmup interval"),
                fit=_interval(intervals["fit"], "fit interval"),
                validation=_interval(intervals["validation"], "validation interval"),
                heldout=_interval(intervals["heldout"], "heldout interval"),
                split_id=split_id,
            )
            if stop - start != fold.heldout.size or stop > equity.height:
                raise WalkForwardResultError("walk-forward held-out interval and equity rows differ")
            fold_equity = equity.slice(start, stop - start)
            folds.append(
                WalkForwardOptimizationFoldResult(
                    fold=fold,
                    seed=payload["seed"],
                    selected_parameters=_object(payload["selected_parameters"], "selected parameters"),
                    validation_cost=payload["validation_cost"],
                    validation_metrics=_object(payload["validation_metrics"], "validation metrics"),
                    heldout_metrics=_object(payload["heldout_metrics"], "held-out metrics"),
                    trials=tuple(_trial(item) for item in _array(payload["trials"], "trials")),
                    failures=tuple(_failure(item) for item in _array(payload["failures"], "failures")),
                    input_sha256=payload["input_sha256"],
                    model_sha256=payload["model_sha256"],
                    _heldout_equity=fold_equity,
                )
            )
            equity_cursor = stop
        if equity_cursor != equity.height:
            raise WalkForwardResultError("walk-forward equity contains unassigned rows")
        return WalkForwardExperimentResult(input_sha256, folds)
    except (KeyError, OSError, TypeError, ValueError, OverflowError, json.JSONDecodeError, pl.exceptions.PolarsError) as error:
        if isinstance(error, WalkForwardResultError):
            raise
        raise WalkForwardResultError(f"invalid walk-forward result: {source_path}") from error
