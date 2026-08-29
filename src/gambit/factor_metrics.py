"""Bounded, process-safe lifetime metrics for the factor cache."""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Mapping

_FORMAT = "gambit-factor-cache-metrics"
_VERSION = 1
COUNTER_NAMES = (
    "cache_hits",
    "cache_misses",
    "cache_admissions",
    "cache_declines",
    "cache_evictions",
    "reclaimed_bytes",
    "corruption_failures",
    "lease_conflicts",
)


class FactorMetricsError(RuntimeError):
    """Persistent factor-cache metrics are malformed or cannot be updated safely."""


@dataclass(frozen=True)
class FactorCacheMetrics:
    format: str
    version: int
    updated_ns: int
    counters: dict[str, int]

    def snapshot(self) -> dict[str, object]:
        return asdict(self)


def _empty_counters() -> dict[str, int]:
    return {name: 0 for name in COUNTER_NAMES}


def _validate_increments(increments: Mapping[str, int]) -> dict[str, int]:
    unknown = set(increments) - set(COUNTER_NAMES)
    if unknown:
        raise ValueError(f"unknown factor-cache counters: {', '.join(sorted(unknown))}")
    normalized = _empty_counters()
    for name, value in increments.items():
        if type(value) is not int or value < 0:
            raise ValueError("factor-cache counter increments must be non-negative integers")
        normalized[name] = value
    return normalized


@contextmanager
def _metrics_lock(root: Path, *, exclusive: bool) -> Iterator[Path]:
    metrics = root / "metrics"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise FactorMetricsError("factor cache root must be a non-symlink directory")
    root.mkdir(parents=True, exist_ok=True)
    if metrics.is_symlink():
        raise FactorMetricsError("factor metrics directory may not be a symbolic link")
    metrics.mkdir(exist_ok=True)
    descriptor = os.open(metrics / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield metrics
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _decode(path: Path) -> FactorCacheMetrics:
    if not path.exists():
        return FactorCacheMetrics(_FORMAT, _VERSION, 0, _empty_counters())
    if path.is_symlink():
        raise FactorMetricsError("factor metrics file may not be a symbolic link")
    try:
        payload = json.loads(path.read_bytes())
        counters = payload["counters"]
        if (
            payload["format"] != _FORMAT
            or payload["version"] != _VERSION
            or type(payload["updated_ns"]) is not int
            or payload["updated_ns"] < 0
            or not isinstance(counters, dict)
            or set(counters) != set(COUNTER_NAMES)
            or any(type(value) is not int or value < 0 for value in counters.values())
        ):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise FactorMetricsError("factor cache lifetime metrics are invalid") from error
    return FactorCacheMetrics(_FORMAT, _VERSION, payload["updated_ns"], dict(counters))


def read_factor_cache_metrics(root: str | Path) -> FactorCacheMetrics:
    store = Path(root)
    with _metrics_lock(store, exclusive=False) as metrics:
        return _decode(metrics / "lifetime.json")


def record_factor_cache_metrics(root: str | Path, **increments: int) -> FactorCacheMetrics:
    """Atomically add fixed-cardinality counters across cooperating processes."""
    additions = _validate_increments(increments)
    store = Path(root)
    with _metrics_lock(store, exclusive=True) as metrics:
        path = metrics / "lifetime.json"
        previous = _decode(path)
        counters = {
            name: previous.counters[name] + additions[name]
            for name in COUNTER_NAMES
        }
        updated = FactorCacheMetrics(_FORMAT, _VERSION, time.time_ns(), counters)
        staging = metrics / f".lifetime-{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(json.dumps(updated.snapshot(), sort_keys=True, separators=(",", ":")).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, path)
            directory = os.open(metrics, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            staging.unlink(missing_ok=True)
        return updated


def try_record_factor_cache_metrics(root: str | Path, **increments: int) -> bool:
    """Best-effort recording for operations whose completed mutation cannot be rolled back."""
    try:
        record_factor_cache_metrics(root, **increments)
    except (FactorMetricsError, OSError):
        return False
    return True


def format_prometheus_metrics(metrics: FactorCacheMetrics) -> str:
    lines = [
        "# HELP gambit_factor_cache_counter Factor cache lifetime operational counter.",
        "# TYPE gambit_factor_cache_counter counter",
    ]
    lines.extend(
        f'gambit_factor_cache_counter{{name="{name}"}} {metrics.counters[name]}'
        for name in COUNTER_NAMES
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "COUNTER_NAMES",
    "FactorCacheMetrics",
    "FactorMetricsError",
    "format_prometheus_metrics",
    "read_factor_cache_metrics",
    "record_factor_cache_metrics",
    "try_record_factor_cache_metrics",
]
