"""Shared strategy callback types kept independent of the strategy orchestrator."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import TypeAlias

import numpy as np

from gambit.pq_types import Contract

StrategyContextType: TypeAlias = SimpleNamespace
PriceFunctionType: TypeAlias = Callable[
    [Contract, np.ndarray, int, StrategyContextType | None],
    float,
]

__all__ = ["PriceFunctionType", "StrategyContextType"]
