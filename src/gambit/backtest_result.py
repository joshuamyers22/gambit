"""Immutable snapshots and telemetry produced by a strategy run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import polars as pl

from gambit.configuration import RunProvenance


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

    def __init__(
        self,
        *,
        provenance: RunProvenance,
        telemetry: BacktestTelemetry,
        trades: pl.DataFrame,
        orders: pl.DataFrame,
        decisions: pl.DataFrame,
        pnl: pl.DataFrame,
    ) -> None:
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "telemetry", telemetry)
        object.__setattr__(self, "_trades", trades.clone())
        object.__setattr__(self, "_orders", orders.clone())
        object.__setattr__(self, "_decisions", decisions.clone())
        object.__setattr__(self, "_pnl", pnl.clone())

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
    def frames(self) -> Mapping[str, pl.DataFrame]:
        return {
            "trades": self.trades,
            "orders": self.orders,
            "decisions": self.decisions,
            "pnl": self.pnl,
        }
