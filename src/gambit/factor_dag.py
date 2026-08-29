"""Polars factor-DAG execution with identity-backed mapped-column reuse."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import polars as pl

from gambit.factor_identity import FactorNodeIdentity
from gambit.factor_store import (
    FactorGenerationLease,
    FactorNodeCacheMiss,
    open_generation_by_node_key,
    publish_factor_node,
)

FactorCompute = Callable[[Mapping[str, pl.DataFrame]], pl.DataFrame]


@dataclass(frozen=True)
class PolarsFactorNode:
    """One topologically ordered factor node and its deterministic computation."""

    identity: FactorNodeIdentity
    compute: FactorCompute


@dataclass(frozen=True)
class FactorDagTelemetry:
    """Cache decisions made during one DAG execution."""

    cache_hits: tuple[str, ...]
    cache_misses: tuple[str, ...]

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

    def __init__(self, cache_root: str | Path) -> None:
        self._cache_root = Path(cache_root)

    def execute(self, nodes: tuple[PolarsFactorNode, ...]) -> FactorDagExecution:
        if not nodes:
            raise ValueError("factor DAG must contain at least one node")
        outputs: dict[str, pl.DataFrame] = {}
        leases: list[FactorGenerationLease] = []
        hits: list[str] = []
        misses: list[str] = []
        try:
            for node in nodes:
                node_key = node.identity.node_key
                if node_key in outputs:
                    raise ValueError("factor DAG contains a duplicate node identity")
                missing_parents = [parent for parent in node.identity.parents if parent not in outputs]
                if missing_parents:
                    raise ValueError("factor DAG nodes must be topologically ordered")
                try:
                    lease = open_generation_by_node_key(self._cache_root, node_key)
                except FactorNodeCacheMiss:
                    parent_outputs = MappingProxyType(
                        {parent: outputs[parent] for parent in node.identity.parents}
                    )
                    frame = node.compute(parent_outputs)
                    self._validate_output(node.identity, frame)
                    columns = {name: frame[name].to_numpy() for name in frame.columns}
                    publish_factor_node(self._cache_root, node.identity, columns)
                    outputs[node_key] = frame
                    misses.append(node_key)
                else:
                    frame = pl.DataFrame(
                        {column.name: lease[column.name].values for column in node.identity.output_schema}
                    )
                    self._validate_output(node.identity, frame)
                    outputs[node_key] = frame
                    leases.append(lease)
                    hits.append(node_key)
        except Exception:
            for lease in leases:
                lease.close()
            raise
        telemetry = FactorDagTelemetry(tuple(hits), tuple(misses))
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
    "PolarsFactorDagExecutor",
    "PolarsFactorNode",
]
