"""Shared strategy callback types kept independent of the strategy orchestrator."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Protocol, TypeAlias

import numpy as np

from gambit.pq_types import Contract

StrategyContextType: TypeAlias = SimpleNamespace
PriceFunctionType: TypeAlias = Callable[
    [Contract, np.ndarray, int, StrategyContextType | None],
    float,
]


class ReturnReporter(Protocol):
    """Analytics and presentation operations used by the strategy facade."""

    def metrics(
        self,
        timestamps: np.ndarray,
        returns: np.ndarray,
        starting_equity: float,
        *,
        periods_per_year: int = 0,
    ) -> dict[str, Any]: ...

    def display(self, metrics: dict[str, Any], *, float_precision: int = 4) -> None: ...

    def plot(self, metrics: dict[str, Any]) -> Any: ...


__all__ = ["PriceFunctionType", "ReturnReporter", "StrategyContextType"]
