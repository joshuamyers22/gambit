"""Typed run configuration and reproducibility metadata."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import polars as pl
import yaml


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class RunConfiguration:
    starting_equity: float = 1.0e6
    pnl_calc_time: int = 16 * 60 + 1
    trade_lag: int = 0
    run_final_calc: bool = True
    log_trades: bool = True
    log_orders: bool = False

    def __post_init__(self) -> None:
        if not math.isfinite(self.starting_equity) or self.starting_equity <= 0:
            raise ValueError("starting_equity must be finite and positive")
        if not 0 <= self.pnl_calc_time < 24 * 60:
            raise ValueError("pnl_calc_time must be between 0 and 1439")
        if isinstance(self.trade_lag, bool) or self.trade_lag < 0:
            raise ValueError("trade_lag must be a non-negative integer")

    @property
    def digest(self) -> str:
        return _digest(asdict(self))

    @classmethod
    def from_layers(cls, *layers: Mapping[str, Any] | None) -> RunConfiguration:
        resolved: dict[str, Any] = {}
        valid_fields = set(cls.__dataclass_fields__)
        for layer in layers:
            if layer is None:
                continue
            unknown = set(layer) - valid_fields
            if unknown:
                raise ValueError(f"unknown run configuration fields: {', '.join(sorted(unknown))}")
            resolved.update(layer)
        return cls(**resolved)


def load_run_configuration(
    *paths: str | Path,
    defaults: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> RunConfiguration:
    """Load optional YAML layers from left to right, then apply overrides."""
    layers: list[Mapping[str, Any] | None] = [defaults]
    for path_value in paths:
        path = Path(path_value)
        if not path.is_file():
            continue
        loaded = yaml.safe_load(path.read_text())
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, Mapping):
            raise ValueError(f"configuration root must be a mapping: {path}")
        layers.append(loaded)
    layers.append(overrides)
    return RunConfiguration.from_layers(*layers)


def fingerprint_polars_frame(frame: pl.DataFrame) -> str:
    """Return a deterministic fingerprint incorporating schema, order, and values."""
    schema = [(name, str(dtype)) for name, dtype in frame.schema.items()]
    row_hashes = frame.hash_rows(seed=0, seed_1=1, seed_2=2, seed_3=3).to_numpy().tobytes()
    digest = hashlib.sha256(_canonical_json(schema).encode())
    digest.update(row_hashes)
    return digest.hexdigest()


def _package_version() -> str:
    try:
        return version("gambit")
    except PackageNotFoundError:
        return "unknown"


def _git_commit(repository: Path | None = None) -> str | None:
    environment_commit = os.environ.get("GITHUB_SHA")
    if environment_commit:
        return environment_commit
    current = (repository or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        git_directory = directory / ".git"
        head = git_directory / "HEAD"
        if not head.is_file():
            continue
        value = head.read_text().strip()
        if not value.startswith("ref: "):
            return value
        ref = git_directory / value.removeprefix("ref: ")
        return ref.read_text().strip() if ref.is_file() else None
    return None


@dataclass(frozen=True)
class RunProvenance:
    configuration: RunConfiguration
    input_fingerprints: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    package_version: str = field(default_factory=_package_version)
    git_commit: str | None = field(default_factory=_git_commit)
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_fingerprints", MappingProxyType(dict(self.input_fingerprints)))

    @property
    def run_fingerprint(self) -> str:
        identity = {
            "configuration": asdict(self.configuration),
            "inputs": dict(sorted(self.input_fingerprints.items())),
            "package_version": self.package_version,
            "git_commit": self.git_commit,
        }
        return _digest(identity)

    def with_input(self, name: str, fingerprint: str) -> RunProvenance:
        if not name or not fingerprint:
            raise ValueError("input name and fingerprint must be non-empty")
        inputs = dict(self.input_fingerprints)
        inputs[name] = fingerprint
        return replace(self, input_fingerprints=inputs)

    def with_polars_input(self, name: str, frame: pl.DataFrame) -> RunProvenance:
        return self.with_input(name, fingerprint_polars_frame(frame))

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable resolved provenance snapshot."""
        return {
            "configuration": asdict(self.configuration),
            "configuration_digest": self.configuration.digest,
            "input_fingerprints": dict(sorted(self.input_fingerprints.items())),
            "package_version": self.package_version,
            "git_commit": self.git_commit,
            "captured_at": self.captured_at.isoformat(),
            "run_fingerprint": self.run_fingerprint,
        }
