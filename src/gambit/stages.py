"""Typed interfaces and dependency metadata for strategy stages."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol, Sequence, runtime_checkable

import numpy as np

from gambit.pq_types import ContractGroup, Order, Trade

if TYPE_CHECKING:
    from gambit.account import Account


@runtime_checkable
class IndicatorStage(Protocol):
    def __call__(
        self,
        contract_group: ContractGroup,
        timestamps: np.ndarray,
        parent_values: SimpleNamespace,
        strategy_context: SimpleNamespace,
    ) -> np.ndarray: ...


@runtime_checkable
class SignalStage(Protocol):
    def __call__(
        self,
        contract_group: ContractGroup,
        timestamps: np.ndarray,
        indicator_values: SimpleNamespace,
        parent_values: SimpleNamespace,
        strategy_context: SimpleNamespace,
    ) -> np.ndarray: ...


@runtime_checkable
class RuleStage(Protocol):
    def __call__(
        self,
        contract_group: ContractGroup,
        index: int,
        timestamps: np.ndarray,
        indicator_values: SimpleNamespace,
        signal_values: np.ndarray,
        account: Account,
        open_orders: Sequence[Order],
        strategy_context: SimpleNamespace,
    ) -> list[Order]: ...


@runtime_checkable
class ExecutionStage(Protocol):
    def __call__(
        self,
        orders: Sequence[Order],
        index: int,
        timestamps: np.ndarray,
        indicators: dict[str, SimpleNamespace],
        signals: dict[str, SimpleNamespace],
        strategy_context: SimpleNamespace,
    ) -> list[Trade]: ...


@runtime_checkable
class AccountingStage(Protocol):
    def position(self, contract_group: ContractGroup, timestamp: np.datetime64) -> float: ...

    def equity(self, timestamp: np.datetime64) -> float: ...

    def add_trades(self, trades: Sequence[Trade]) -> None: ...


@dataclass(frozen=True)
class StageNode:
    name: str
    kind: str
    dependencies: tuple[str, ...] = ()


class StageGraph:
    """Small deterministic DAG used to validate and describe a strategy pipeline."""

    def __init__(self, nodes: Sequence[StageNode]) -> None:
        self._nodes = {node.name: node for node in nodes}
        if len(self._nodes) != len(nodes):
            raise ValueError("stage names must be unique")

    @property
    def nodes(self) -> tuple[StageNode, ...]:
        return tuple(self._nodes.values())

    def topological_order(self) -> tuple[str, ...]:
        missing = sorted(
            {dependency for node in self._nodes.values() for dependency in node.dependencies if dependency not in self._nodes}
        )
        if missing:
            raise ValueError(f"stage dependencies are missing: {', '.join(missing)}")

        order: list[str] = []
        visiting: list[str] = []
        complete: set[str] = set()

        def visit(name: str) -> None:
            if name in complete:
                return
            if name in visiting:
                cycle = visiting[visiting.index(name) :] + [name]
                raise ValueError(f"stage dependency cycle: {' -> '.join(cycle)}")
            visiting.append(name)
            for dependency in self._nodes[name].dependencies:
                visit(dependency)
            visiting.pop()
            complete.add(name)
            order.append(name)

        for name in self._nodes:
            visit(name)
        return tuple(order)

    def describe(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {"name": node.name, "kind": node.kind, "dependencies": list(node.dependencies)}
            for node in self._nodes.values()
        )
