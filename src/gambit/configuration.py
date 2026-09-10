"""Typed run configuration and reproducibility metadata."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from numbers import Integral, Real
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
        if isinstance(self.starting_equity, bool) or not isinstance(self.starting_equity, Real):
            raise TypeError("starting_equity must be a real number, not a boolean")
        try:
            finite_equity = math.isfinite(self.starting_equity)
        except OverflowError:
            finite_equity = False
        if not finite_equity or self.starting_equity <= 0:
            raise ValueError("starting_equity must be finite and positive")
        if type(self.starting_equity) not in (int, float):
            object.__setattr__(self, "starting_equity", float(self.starting_equity))
        for name in ("pnl_calc_time", "trade_lag"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise TypeError(f"{name} must be an integer, not a boolean")
            object.__setattr__(self, name, int(value))
        for name in ("run_final_calc", "log_trades", "log_orders"):
            if type(getattr(self, name)) is not bool:
                raise TypeError(f"{name} must be a bool")
        if not 0 <= self.pnl_calc_time < 24 * 60:
            raise ValueError("pnl_calc_time must be between 0 and 1439")
        if self.trade_lag < 0:
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
            if not isinstance(layer, Mapping) or any(not isinstance(key, str) for key in layer):
                raise TypeError("configuration layers must be mappings with string keys")
            unknown = set(layer) - valid_fields
            if unknown:
                raise ValueError(f"unknown run configuration fields: {', '.join(sorted(unknown))}")
            resolved.update(layer)
        return cls(**resolved)


class _ConfigurationLoader(yaml.SafeLoader):
    """Reject duplicate YAML fields rather than silently replacing a setting."""


def _unique_configuration_mapping(loader: _ConfigurationLoader, node: yaml.MappingNode) -> dict:
    loader.flatten_mapping(node)
    result: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise TypeError("configuration keys must be strings")
        if key in result:
            raise ValueError(f"duplicate configuration field: {key}")
        result[key] = loader.construct_object(value_node)
    return result


_ConfigurationLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_configuration_mapping)


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
        loaded = yaml.load(path.read_text(), Loader=_ConfigurationLoader)
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
        return version("gambit-markets")
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
    # Canonical JSON is immutable and preserves nested snapshots without retaining callbacks.
    execution_manifest_json: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_fingerprints", MappingProxyType(dict(self.input_fingerprints)))
        if self.execution_manifest_json is not None:
            manifest = json.loads(self.execution_manifest_json)
            if (not isinstance(manifest, dict) or type(manifest.get("version")) is not int
                    or manifest["version"] != 1):
                raise ValueError("unsupported execution manifest")
            object.__setattr__(self, "execution_manifest_json", _canonical_json(manifest))

    @property
    def run_fingerprint(self) -> str:
        identity = {
            "configuration": asdict(self.configuration),
            "inputs": dict(sorted(self.input_fingerprints.items())),
            "package_version": self.package_version,
            "git_commit": self.git_commit,
        }
        if self.execution_manifest_json is not None:
            identity["execution_manifest"] = json.loads(self.execution_manifest_json)
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
        snapshot = {
            "configuration": asdict(self.configuration),
            "configuration_digest": self.configuration.digest,
            "input_fingerprints": dict(sorted(self.input_fingerprints.items())),
            "package_version": self.package_version,
            "git_commit": self.git_commit,
            "captured_at": self.captured_at.isoformat(),
            "run_fingerprint": self.run_fingerprint,
        }
        if self.execution_manifest_json is not None:
            snapshot["execution_manifest"] = json.loads(self.execution_manifest_json)
        return snapshot
