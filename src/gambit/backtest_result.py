"""Immutable snapshots and telemetry produced by a strategy run."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

import polars as pl

from gambit.configuration import RunConfiguration, RunProvenance

BUNDLE_FORMAT = "gambit.backtest-result"
BUNDLE_VERSION = 2
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


class BacktestBundleError(ValueError):
    """Raised when a persisted result bundle is invalid or corrupted."""


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
    def load(cls, source: str | Path) -> BacktestResult:
        """Load a result bundle after validating its format, schema, and hashes."""
        source_path = Path(source)
        try:
            manifest = json.loads((source_path / "manifest.json").read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise BacktestBundleError(f"cannot read result manifest: {source_path}") from error
        if manifest.get("format") != BUNDLE_FORMAT or manifest.get("version") != BUNDLE_VERSION:
            raise BacktestBundleError("unsupported backtest result bundle format or version")

        frames: dict[str, pl.DataFrame] = {}
        try:
            frame_manifest = manifest["frames"]
            if set(frame_manifest) != set(_FRAME_NAMES):
                raise BacktestBundleError("result bundle has an unexpected frame set")
            for name in _FRAME_NAMES:
                metadata = frame_manifest[name]
                expected_filename = f"{name}.arrow"
                if metadata["file"] != expected_filename:
                    raise BacktestBundleError(f"invalid filename for frame: {name}")
                path = source_path / expected_filename
                if _file_digest(path) != metadata["sha256"]:
                    raise BacktestBundleError(f"checksum mismatch for frame: {name}")
                frame = pl.read_ipc(path, memory_map=False)
                if frame.height != metadata["rows"] or _frame_schema(frame) != metadata["schema"]:
                    raise BacktestBundleError(f"shape or schema mismatch for frame: {name}")
                frames[name] = frame

            provenance_data = manifest["provenance"]
            provenance = RunProvenance(
                configuration=RunConfiguration(**provenance_data["configuration"]),
                input_fingerprints=provenance_data["input_fingerprints"],
                package_version=provenance_data["package_version"],
                git_commit=provenance_data["git_commit"],
                captured_at=datetime.fromisoformat(provenance_data["captured_at"]),
            )
            if provenance.configuration.digest != provenance_data["configuration_digest"]:
                raise BacktestBundleError("configuration digest mismatch")
            if provenance.run_fingerprint != provenance_data["run_fingerprint"]:
                raise BacktestBundleError("provenance fingerprint mismatch")
            telemetry_data = manifest["telemetry"]
            telemetry = BacktestTelemetry(
                stages=tuple(StageTelemetry(**stage) for stage in telemetry_data.pop("stages")),
                **telemetry_data,
            )
        except (KeyError, OSError, TypeError, ValueError, pl.exceptions.PolarsError) as error:
            if isinstance(error, BacktestBundleError):
                raise
            raise BacktestBundleError(f"invalid result bundle: {source_path}") from error
        return cls(provenance=provenance, telemetry=telemetry, **frames)
