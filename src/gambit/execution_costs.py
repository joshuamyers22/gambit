"""Composable slippage, market-impact, commission, and fee models."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from gambit.pq_types import Order


class SlippageModel(Protocol):
    def adjustment(self, order: Order, raw_price: float) -> float: ...


class ChargeModel(Protocol):
    def charge(self, order: Order, execution_price: float) -> float: ...


@dataclass(frozen=True)
class FixedPercentageSlippage:
    percentage: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.percentage) or self.percentage < 0:
            raise ValueError("slippage percentage must be finite and non-negative")

    def adjustment(self, order: Order, raw_price: float) -> float:
        return math.copysign(abs(raw_price) * self.percentage, order.qty)


@dataclass(frozen=True)
class BidAskSpreadSlippage:
    """Cross half of a quoted absolute bid/ask spread."""

    spread: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.spread) or self.spread < 0:
            raise ValueError("spread must be finite and non-negative")

    def adjustment(self, order: Order, raw_price: float) -> float:
        del raw_price
        return math.copysign(self.spread / 2, order.qty)


@dataclass(frozen=True)
class SquareRootMarketImpact:
    """Apply impact proportional to volatility and square-root participation."""

    available_volume: float
    volatility: float
    coefficient: float = 1.0

    def __post_init__(self) -> None:
        values = (self.available_volume, self.volatility, self.coefficient)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("market-impact inputs must be finite")
        if self.available_volume <= 0 or self.volatility < 0 or self.coefficient < 0:
            raise ValueError("volume must be positive; volatility and coefficient must be non-negative")

    def adjustment(self, order: Order, raw_price: float) -> float:
        participation = abs(order.qty) / self.available_volume
        impact_fraction = self.coefficient * self.volatility * math.sqrt(participation)
        return math.copysign(abs(raw_price) * impact_fraction, order.qty)


@dataclass(frozen=True)
class PerUnitCharge:
    amount: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.amount) or self.amount < 0:
            raise ValueError("per-unit charge must be finite and non-negative")

    def charge(self, order: Order, execution_price: float) -> float:
        del execution_price
        return self.amount * abs(order.qty)


@dataclass(frozen=True)
class PerOrderCharge:
    amount: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.amount) or self.amount < 0:
            raise ValueError("per-order charge must be finite and non-negative")

    def charge(self, order: Order, execution_price: float) -> float:
        del order, execution_price
        return self.amount


@dataclass(frozen=True)
class NotionalCharge:
    rate: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.rate) or self.rate < 0:
            raise ValueError("notional charge rate must be finite and non-negative")

    def charge(self, order: Order, execution_price: float) -> float:
        return self.rate * abs(order.qty * execution_price * order.contract.multiplier)
