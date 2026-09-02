"""Polars factor-DAG execution with identity-backed mapped-column reuse."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import polars as pl

from gambit.factor_admission import clear_rejection, has_recent_rejection, record_rejection
from gambit.factor_identity import FactorNodeIdentity
from gambit.factor_metrics import try_record_factor_cache_metrics
from gambit.factor_store import (
    FactorGenerationLease,
    FactorNodeCacheMiss,
    FactorStoreError,
    open_generation_by_node_key,
    publish_factor_node,
)

FactorCompute = Callable[[Mapping[str, pl.DataFrame]], pl.DataFrame]


@dataclass(frozen=True)
class FactorCacheAdmissionPolicy:
    """Estimate whether publication will repay its write and future read costs."""

    minimum_expected_uses: int = 2
    estimated_read_bytes_per_second: float = 2 * 1024 * 1024 * 1024
    estimated_write_bytes_per_second: float = 1024 * 1024 * 1024
    fixed_read_seconds: float = 0.00025
    fixed_write_seconds: float = 0.0005
    minimum_speedup: float = 1.1
    rejection_ttl_seconds: float = 3600.0
    force_admission: bool = False

    def __post_init__(self) -> None:
        if self.minimum_expected_uses < 1:
            raise ValueError("minimum_expected_uses must be positive")
        if (
            not math.isfinite(self.estimated_read_bytes_per_second)
            or not math.isfinite(self.estimated_write_bytes_per_second)
            or self.estimated_read_bytes_per_second <= 0
            or self.estimated_write_bytes_per_second <= 0
        ):
            raise ValueError("estimated cache bandwidth must be positive")
        if (
            not math.isfinite(self.fixed_read_seconds)
            or not math.isfinite(self.fixed_write_seconds)
            or self.fixed_read_seconds < 0
            or self.fixed_write_seconds < 0
        ):
            raise ValueError("fixed cache costs must be non-negative")
        if not math.isfinite(self.minimum_speedup) or self.minimum_speedup <= 0:
            raise ValueError("minimum_speedup must be positive")
        if not math.isfinite(self.rejection_ttl_seconds) or self.rejection_ttl_seconds < 0:
            raise ValueError("rejection_ttl_seconds must be finite and non-negative")

    @classmethod
    def always(cls) -> FactorCacheAdmissionPolicy:
        """Preserve unconditional caching for experiments and compatibility."""
        return cls(force_admission=True)

    def admit(self, *, compute_seconds: float, output_bytes: int, expected_uses: int) -> bool:
        if not math.isfinite(compute_seconds) or compute_seconds < 0 or output_bytes < 0 or expected_uses < 1:
            raise ValueError("cache admission measurements must be non-negative")
        if self.force_admission:
            return True
        if expected_uses < self.minimum_expected_uses:
            return False
        read_seconds = self.fixed_read_seconds + output_bytes / self.estimated_read_bytes_per_second
        write_seconds = self.fixed_write_seconds + output_bytes / self.estimated_write_bytes_per_second
        uncached_seconds = compute_seconds * expected_uses
        cached_seconds = compute_seconds + write_seconds + read_seconds * (expected_uses - 1)
        return uncached_seconds >= cached_seconds * self.minimum_speedup

    @property
    def policy_key(self) -> str:
        value = {
            "format": "gambit-factor-admission-policy",
            "version": 1,
            "minimum_expected_uses": self.minimum_expected_uses,
            "estimated_read_bytes_per_second": self.estimated_read_bytes_per_second,
            "estimated_write_bytes_per_second": self.estimated_write_bytes_per_second,
            "fixed_read_seconds": self.fixed_read_seconds,
            "fixed_write_seconds": self.fixed_write_seconds,
            "minimum_speedup": self.minimum_speedup,
            "force_admission": self.force_admission,
        }
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class PolarsFactorNode:
    """One topologically ordered factor node and its deterministic computation."""

    identity: FactorNodeIdentity
    compute: FactorCompute
    expected_uses: int = 2

    def __post_init__(self) -> None:
        if self.expected_uses < 1:
            raise ValueError("expected_uses must be positive")


@dataclass(frozen=True)
class FactorDagTelemetry:
    """Cache decisions made during one DAG execution."""

    cache_hits: tuple[str, ...]
    cache_misses: tuple[str, ...]
    cache_writes: tuple[str, ...] = ()
    cache_declines: tuple[str, ...] = ()
    rejection_hints: tuple[str, ...] = ()
    compute_measurements: tuple[tuple[str, float, int], ...] = ()
    lifetime_metrics_recorded: bool = True

    @property
    def nodes_reused(self) -> int:
        return len(self.cache_hits)

    @property
    def nodes_computed(self) -> int:
        return len(self.cache_misses)


class FactorDagExecution(Mapping[str, pl.DataFrame]):
    """DAG outputs that pin every mapped cache-hit generation until closed."""

    def __init__(
        self,
        outputs: dict[str, pl.DataFrame],
        leases: list[FactorGenerationLease],
        telemetry: FactorDagTelemetry,
    ) -> None:
        self._outputs = outputs
        self._view = MappingProxyType(outputs)
        self._leases = leases
        self.telemetry = telemetry
        self._closed = False

    def __getitem__(self, node_key: str) -> pl.DataFrame:
        if self._closed:
            raise RuntimeError("factor DAG execution is closed")
        return self._view[node_key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._view)

    def __len__(self) -> int:
        return len(self._view)

    def close(self) -> None:
        if self._closed:
            return
        self._outputs.clear()
        for lease in self._leases:
            lease.close()
        self._leases.clear()
        self._closed = True

    def __enter__(self) -> FactorDagExecution:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


class PolarsFactorDagExecutor:
    """Execute topologically sorted nodes while reusing valid mapped generations."""

    def __init__(
        self,
        cache_root: str | Path,
        admission_policy: FactorCacheAdmissionPolicy | None = None,
    ) -> None:
        self._cache_root = Path(cache_root)
        self._admission_policy = admission_policy or FactorCacheAdmissionPolicy()

    def execute(self, nodes: tuple[PolarsFactorNode, ...]) -> FactorDagExecution:
        if not nodes:
            raise ValueError("factor DAG must contain at least one node")
        outputs: dict[str, pl.DataFrame] = {}
        leases: list[FactorGenerationLease] = []
        hits: list[str] = []
        misses: list[str] = []
        writes: list[str] = []
        declines: list[str] = []
        rejection_hints: list[str] = []
        measurements: list[tuple[str, float, int]] = []
        try:
            for node in nodes:
                node_key = node.identity.node_key
                if node_key in outputs:
                    raise ValueError("factor DAG contains a duplicate node identity")
                missing_parents = [parent for parent in node.identity.parents if parent not in outputs]
                if missing_parents:
                    raise ValueError("factor DAG nodes must be topologically ordered")
                rejected_hint = has_recent_rejection(
                    self._cache_root,
                    node_key,
                    self._admission_policy.policy_key,
                    ttl_seconds=self._admission_policy.rejection_ttl_seconds,
                )
                if (self._cache_root / "nodes" / node_key).exists():
                    rejected_hint = False
                lease = None
                if rejected_hint:
                    rejection_hints.append(node_key)
                else:
                    try:
                        lease = open_generation_by_node_key(self._cache_root, node_key)
                    except FactorNodeCacheMiss:
                        pass
                    except FactorStoreError:
                        try_record_factor_cache_metrics(self._cache_root, corruption_failures=1)
                        raise
                if lease is None:
                    parent_outputs = MappingProxyType(
                        {parent: outputs[parent] for parent in node.identity.parents}
                    )
                    compute_started = time.perf_counter()
                    frame = node.compute(parent_outputs)
                    compute_seconds = time.perf_counter() - compute_started
                    self._validate_output(node.identity, frame)
                    output_bytes = frame.estimated_size()
                    measurements.append((node_key, compute_seconds, output_bytes))
                    if self._admission_policy.admit(
                        compute_seconds=compute_seconds,
                        output_bytes=output_bytes,
                        expected_uses=node.expected_uses,
                    ):
                        columns = {name: frame[name].to_numpy() for name in frame.columns}
                        publish_factor_node(self._cache_root, node.identity, columns)
                        clear_rejection(self._cache_root, node_key)
                        writes.append(node_key)
                    else:
                        if not rejected_hint:
                            record_rejection(
                                self._cache_root,
                                node_key,
                                self._admission_policy.policy_key,
                                compute_seconds=compute_seconds,
                                output_bytes=output_bytes,
                            )
                        declines.append(node_key)
                    outputs[node_key] = frame
                    misses.append(node_key)
                else:
                    clear_rejection(self._cache_root, node_key)
                    frame = pl.DataFrame(
                        {column.name: lease[column.name].values for column in node.identity.output_schema}
                    )
                    self._validate_output(node.identity, frame)
                    outputs[node_key] = frame
                    leases.append(lease)
                    hits.append(node_key)
        except BaseException:  # cached leases must close on cancellation and interpreter exit before reraising
            for lease in leases:
                lease.close()
            raise
        metrics_recorded = try_record_factor_cache_metrics(
            self._cache_root,
            cache_hits=len(hits),
            cache_misses=len(misses),
            cache_admissions=len(writes),
            cache_declines=len(declines),
        )
        telemetry = FactorDagTelemetry(
            tuple(hits),
            tuple(misses),
            tuple(writes),
            tuple(declines),
            tuple(rejection_hints),
            tuple(measurements),
            metrics_recorded,
        )
        return FactorDagExecution(outputs, leases, telemetry)

    @staticmethod
    def _validate_output(identity: FactorNodeIdentity, frame: pl.DataFrame) -> None:
        expected_names = [column.name for column in identity.output_schema]
        if frame.columns != expected_names:
            raise ValueError("factor node output columns do not match its identity schema")
        if any(dtype != pl.Float64 for dtype in frame.dtypes):
            raise ValueError("mapped factor DAG output columns must use Polars Float64")
        if any(frame[name].null_count() for name in frame.columns):
            raise ValueError("mapped factor DAG output columns must not contain Polars nulls")


__all__ = [
    "FactorDagExecution",
    "FactorDagTelemetry",
    "FactorCacheAdmissionPolicy",
    "PolarsFactorDagExecutor",
    "PolarsFactorNode",
]
