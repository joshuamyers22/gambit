"""Immutable snapshots and telemetry produced by a strategy run."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

import polars as pl

from gambit.bundle_limits import BacktestBundleError, BundleLoadLimits
from gambit.configuration import RunConfiguration, RunProvenance
from gambit.ipc_validation import inspect_ipc

BUNDLE_FORMAT = "gambit.backtest-result"
BUNDLE_VERSION = 4
_FRAME_NAMES = (
    "trades",
    "orders",
    "decisions",
    "pnl",
    "risk_measures",
    "risk_exposures",
    "risk_attribution",
    "stress_results",
    "validation_findings",
)


def _bounded_read(path: Path, maximum: int) -> bytes:
    """Open once, reject special/symlink members, and cap even a growing file."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise BacktestBundleError(f"bundle member must be a regular file: {path.name}")
        if info.st_size > maximum:
            raise BacktestBundleError(f"byte limit exceeded: {path.name}")
        data = source.read(maximum + 1)
        if len(data) > maximum:
            raise BacktestBundleError(f"byte limit exceeded: {path.name}")
        return data


def _unique_json(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise BacktestBundleError(f"duplicate manifest key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise BacktestBundleError(f"non-finite manifest number: {value}")


def _object(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise BacktestBundleError(f"{name} must be an object")
    return value


def _validate_frame_manifest(value: object, limits: BundleLoadLimits) -> dict:
    frames = _object(value, "frames")
    if set(frames) != set(_FRAME_NAMES):
        raise BacktestBundleError("result bundle has an unexpected frame set")
    total_rows = 0
    for name, value in frames.items():
        metadata = _object(value, f"frame {name}")
        if metadata.get("file") != f"{name}.arrow":
            raise BacktestBundleError(f"invalid filename for frame: {name}")
        digest = metadata.get("sha256")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise BacktestBundleError(f"invalid sha256 for frame: {name}")
        rows = metadata.get("rows")
        if type(rows) is not int or not 0 <= rows <= limits.max_table_rows:
            raise BacktestBundleError(f"row count or limit invalid: {name}")
        total_rows += rows
        if total_rows > limits.max_total_rows:
            raise BacktestBundleError("total row limit exceeded")
        schema = metadata.get("schema")
        if not isinstance(schema, list) or len(schema) > limits.max_columns:
            raise BacktestBundleError(f"schema column limit or type invalid: {name}")
        names = set()
        for column in schema:
            column = _object(column, f"schema column in {name}")
            if (set(column) != {"name", "dtype"}
                    or any(not isinstance(v, str) or len(v.encode("utf-8")) > 4096 for v in column.values())
                    or column["name"] in names):
                raise BacktestBundleError(f"invalid schema for frame: {name}")
            names.add(column["name"])
    return frames


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _frame_schema(frame: pl.DataFrame) -> list[dict[str, str]]:
    return [{"name": name, "dtype": str(dtype)} for name, dtype in frame.schema.items()]


def _fsync_directory(path: Path) -> None:
    """Persist directory entries where the operating system supports it."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class StageTelemetry:
    """Timing and work counts for one top-level backtest phase."""

    name: str
    elapsed_seconds: float
    cpu_seconds: float
    units: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("stage name must be non-empty")
        if self.elapsed_seconds < 0 or self.cpu_seconds < 0 or self.units < 0:
            raise ValueError("stage telemetry values must be non-negative")


@dataclass(frozen=True)
class BacktestTelemetry:
    """Stable performance and lifecycle counters for a completed run."""

    stages: tuple[StageTelemetry, ...]
    timestamps_processed: int
    orders_proposed: int
    orders_accepted: int
    orders_rejected: int
    orders_filled: int
    orders_cancelled: int
    orders_open: int
    trades_executed: int

    def __post_init__(self) -> None:
        counters = (
            self.timestamps_processed,
            self.orders_proposed,
            self.orders_accepted,
            self.orders_rejected,
            self.orders_filled,
            self.orders_cancelled,
            self.orders_open,
            self.trades_executed,
        )
        if any(value < 0 for value in counters):
            raise ValueError("backtest telemetry counters must be non-negative")
        names = [stage.name for stage in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("backtest telemetry stage names must be unique")

    @property
    def elapsed_seconds(self) -> float:
        return sum(stage.elapsed_seconds for stage in self.stages)

    @property
    def cpu_seconds(self) -> float:
        return sum(stage.cpu_seconds for stage in self.stages)

    def stage(self, name: str) -> StageTelemetry:
        try:
            return next(stage for stage in self.stages if stage.name == name)
        except StopIteration as error:
            raise KeyError(name) from error


@dataclass(frozen=True, init=False)
class BacktestResult:
    """Read-only, detached snapshot of a completed backtest.

    Polars frames are cloned on input and access so callers cannot mutate the
    stored result through shared buffers or object references.
    """

    provenance: RunProvenance
    telemetry: BacktestTelemetry
    _trades: pl.DataFrame
    _orders: pl.DataFrame
    _decisions: pl.DataFrame
    _pnl: pl.DataFrame
    _risk_measures: pl.DataFrame
    _risk_exposures: pl.DataFrame
    _risk_attribution: pl.DataFrame
    _stress_results: pl.DataFrame
    _validation_findings: pl.DataFrame

    def __init__(
        self,
        *,
        provenance: RunProvenance,
        telemetry: BacktestTelemetry,
        trades: pl.DataFrame,
        orders: pl.DataFrame,
        decisions: pl.DataFrame,
        pnl: pl.DataFrame,
        risk_measures: pl.DataFrame,
        risk_exposures: pl.DataFrame,
        risk_attribution: pl.DataFrame,
        stress_results: pl.DataFrame,
        validation_findings: pl.DataFrame,
    ) -> None:
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "telemetry", telemetry)
        object.__setattr__(self, "_trades", trades.clone())
        object.__setattr__(self, "_orders", orders.clone())
        object.__setattr__(self, "_decisions", decisions.clone())
        object.__setattr__(self, "_pnl", pnl.clone())
        object.__setattr__(self, "_risk_measures", risk_measures.clone())
        object.__setattr__(self, "_risk_exposures", risk_exposures.clone())
        object.__setattr__(self, "_risk_attribution", risk_attribution.clone())
        object.__setattr__(self, "_stress_results", stress_results.clone())
        object.__setattr__(self, "_validation_findings", validation_findings.clone())

    @property
    def trades(self) -> pl.DataFrame:
        return self._trades.clone()

    @property
    def orders(self) -> pl.DataFrame:
        return self._orders.clone()

    @property
    def decisions(self) -> pl.DataFrame:
        return self._decisions.clone()

    @property
    def pnl(self) -> pl.DataFrame:
        return self._pnl.clone()

    @property
    def risk_measures(self) -> pl.DataFrame:
        return self._risk_measures.clone()

    @property
    def risk_exposures(self) -> pl.DataFrame:
        return self._risk_exposures.clone()

    @property
    def risk_attribution(self) -> pl.DataFrame:
        return self._risk_attribution.clone()

    @property
    def stress_results(self) -> pl.DataFrame:
        return self._stress_results.clone()

    @property
    def validation_findings(self) -> pl.DataFrame:
        return self._validation_findings.clone()

    @property
    def frames(self) -> Mapping[str, pl.DataFrame]:
        return {
            "trades": self.trades,
            "orders": self.orders,
            "decisions": self.decisions,
            "pnl": self.pnl,
            "risk_measures": self.risk_measures,
            "risk_exposures": self.risk_exposures,
            "risk_attribution": self.risk_attribution,
            "stress_results": self.stress_results,
            "validation_findings": self.validation_findings,
        }

    def save(self, destination: str | Path) -> Path:
        """Atomically publish a versioned, checksummed result bundle.

        The destination must not already exist. This prevents an interrupted
        replacement from destroying a previously valid research artifact.
        """
        destination_path = Path(destination)
        if destination_path.exists():
            raise FileExistsError(destination_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = Path(
            tempfile.mkdtemp(prefix=f".{destination_path.name}.", dir=destination_path.parent)
        )
        try:
            frame_manifest: dict[str, dict[str, object]] = {}
            for name, frame in self.frames.items():
                filename = f"{name}.arrow"
                path = temporary_path / filename
                frame.write_ipc(path, compression="uncompressed")
                with path.open("rb") as persisted_frame:
                    os.fsync(persisted_frame.fileno())
                frame_manifest[name] = {
                    "file": filename,
                    "sha256": _file_digest(path),
                    "rows": frame.height,
                    "schema": _frame_schema(frame),
                }

            payload = {
                "format": BUNDLE_FORMAT,
                "version": BUNDLE_VERSION,
                "provenance": self.provenance.snapshot(),
                "telemetry": asdict(self.telemetry),
                "frames": frame_manifest,
            }
            manifest_path = temporary_path / "manifest.json"
            manifest_path.write_bytes(_canonical_json(payload) + b"\n")
            with manifest_path.open("rb") as manifest:
                os.fsync(manifest.fileno())
            _fsync_directory(temporary_path)
            os.replace(temporary_path, destination_path)
            _fsync_directory(destination_path.parent)
        except BaseException:  # cleanup must also run for cancellation and interpreter exit
            if temporary_path.exists():
                for child in temporary_path.iterdir():
                    child.unlink()
                temporary_path.rmdir()
            raise
        return destination_path

    @classmethod
    def load(cls, source: str | Path, *, limits: BundleLoadLimits | None = None) -> BacktestResult:
        """Load a bounded local bundle of flat, uncompressed Arrow tables.

        All files, hashes, metadata, and allocation estimates are checked before
        any table is materialized. Versions 2–4 share this admission policy.
        """
        if limits is None:
            limits = BundleLoadLimits()
        if not isinstance(limits, BundleLoadLimits):
            raise TypeError("limits must be a BundleLoadLimits")
        source_path = Path(source)
        try:
            manifest = _object(json.loads(
                _bounded_read(source_path / "manifest.json", limits.max_manifest_bytes),
                object_pairs_hook=_unique_json, parse_constant=_reject_constant,
            ), "manifest")
        except (OSError, ValueError, RecursionError) as error:
            if isinstance(error, BacktestBundleError):
                raise
            raise BacktestBundleError(f"cannot read result manifest: {source_path}") from error
        if (manifest.get("format") != BUNDLE_FORMAT or type(manifest.get("version")) is not int
                or manifest["version"] not in (2, 3, BUNDLE_VERSION)):
            raise BacktestBundleError("unsupported backtest result bundle format or version")

        frames: dict[str, pl.DataFrame] = {}
        try:
            frame_manifest = _validate_frame_manifest(manifest["frames"], limits)
            provenance_data = _object(manifest["provenance"], "provenance")
            _object(provenance_data["configuration"], "configuration")
            fingerprints = _object(provenance_data["input_fingerprints"], "input_fingerprints")
            if any(not isinstance(v, str) for v in fingerprints.values()):
                raise BacktestBundleError("input fingerprints must be strings")
            for key in ("package_version", "captured_at", "configuration_digest", "run_fingerprint"):
                if not isinstance(provenance_data[key], str):
                    raise BacktestBundleError(f"provenance {key} must be a string")
            if provenance_data["git_commit"] is not None and not isinstance(provenance_data["git_commit"], str):
                raise BacktestBundleError("provenance git_commit must be a string or null")
            provenance = RunProvenance(
                configuration=RunConfiguration(**provenance_data["configuration"]),
                input_fingerprints=provenance_data["input_fingerprints"],
                package_version=provenance_data["package_version"],
                git_commit=provenance_data["git_commit"],
                captured_at=datetime.fromisoformat(provenance_data["captured_at"]),
                execution_manifest_json=(json.dumps(provenance_data["execution_manifest"], allow_nan=False)
                                         if "execution_manifest" in provenance_data else None),
            )
            if provenance.configuration.digest != provenance_data["configuration_digest"]:
                raise BacktestBundleError("configuration digest mismatch")
            if provenance.run_fingerprint != provenance_data["run_fingerprint"]:
                raise BacktestBundleError("provenance fingerprint mismatch")
            telemetry_data = dict(_object(manifest["telemetry"], "telemetry"))
            stages = telemetry_data.pop("stages")
            if not isinstance(stages, list):
                raise BacktestBundleError("telemetry stages must be an array")
            for stage in stages:
                stage = _object(stage, "telemetry stage")
                if not isinstance(stage["name"], str):
                    raise BacktestBundleError("stage name must be a string")
                if type(stage["units"]) is not int or stage["units"] < 0:
                    raise BacktestBundleError("stage units must be a nonnegative integer")
                for key in ("elapsed_seconds", "cpu_seconds"):
                    value = stage[key]
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise BacktestBundleError(f"invalid stage {key}")
            if any(type(v) is not int or v < 0 for v in telemetry_data.values()):
                raise BacktestBundleError("telemetry counters must be nonnegative integers")
            telemetry = BacktestTelemetry(
                stages=tuple(StageTelemetry(**stage) for stage in stages),
                **telemetry_data,
            )
            payloads: dict[str, bytes] = {}
            total_bytes = total_decoded = 0
            for name in _FRAME_NAMES:
                metadata = frame_manifest[name]
                data = _bounded_read(source_path / metadata["file"],
                                     min(limits.max_table_bytes, limits.max_total_bytes - total_bytes))
                total_bytes += len(data)
                if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
                    raise BacktestBundleError(f"checksum mismatch for frame: {name}")
                try:
                    decoded = inspect_ipc(data, metadata["schema"], metadata["rows"], limits)
                except BacktestBundleError as error:
                    raise BacktestBundleError(f"frame {name}: {error}") from error
                total_decoded += decoded
                if total_decoded > limits.max_total_decoded_bytes:
                    raise BacktestBundleError("total decoded byte limit exceeded")
                payloads[name] = data
            # Decode exactly the checked snapshots: no path reopen or concurrent
            # writer can substitute unchecked content between preflight and use.
            for name in _FRAME_NAMES:
                metadata = frame_manifest[name]
                frame = pl.read_ipc(payloads.pop(name), memory_map=False)
                if frame.height != metadata["rows"] or _frame_schema(frame) != metadata["schema"]:
                    raise BacktestBundleError(f"shape or schema mismatch for frame: {name}")
                frames[name] = frame
        except (KeyError, OSError, TypeError, ValueError, OverflowError, RecursionError, pl.exceptions.PolarsError) as error:
            if isinstance(error, BacktestBundleError):
                raise
            raise BacktestBundleError(f"invalid result bundle: {source_path}") from error
        return cls(provenance=provenance, telemetry=telemetry, **frames)
