"""Explicit calculation parameters shared by pricing and risk operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, cast

import numpy as np


def _to_nanosecond_timestamp(value: np.datetime64) -> np.datetime64:
    """Normalize timestamp precision at the NumPy typing boundary."""
    return cast(np.datetime64, value.astype("datetime64[ns]"))


class MissingDataPolicy(str, Enum):
    ERROR = "error"
    WARN = "warn"
    SKIP = "skip"


class CalculationMode(str, Enum):
    SINGLE = "single"
    HISTORICAL = "historical"


class StressScenarioContract(Protocol):
    """Behavior required by calculation contexts without depending on a reporting adapter."""

    @property
    def name(self) -> str: ...

    def pnl_for(self, row: Mapping[str, object]) -> float: ...


@dataclass(frozen=True)
class CalculationContext:
    valuation_time: np.datetime64
    market_data_as_of: np.datetime64 | None = None
    start_time: np.datetime64 | None = None
    end_time: np.datetime64 | None = None
    calendar: str | None = None
    base_currency: str = "USD"
    scenarios: tuple[StressScenarioContract, ...] = ()
    missing_data_policy: MissingDataPolicy = MissingDataPolicy.ERROR
    mode: CalculationMode = CalculationMode.SINGLE
    allow_lookahead: bool = False
    provenance_reference: str | None = None

    def __post_init__(self) -> None:
        valuation_time = _to_nanosecond_timestamp(self.valuation_time)
        if np.isnat(valuation_time):
            raise ValueError("valuation_time cannot be NaT")
        market_data_as_of = (
            valuation_time
            if self.market_data_as_of is None
            else _to_nanosecond_timestamp(self.market_data_as_of)
        )
        if np.isnat(market_data_as_of):
            raise ValueError("market_data_as_of cannot be NaT")
        if not self.allow_lookahead and market_data_as_of > valuation_time:
            raise ValueError("market_data_as_of cannot be after valuation_time unless look-ahead is enabled")

        start = None if self.start_time is None else _to_nanosecond_timestamp(self.start_time)
        end = None if self.end_time is None else _to_nanosecond_timestamp(self.end_time)
        if start is not None and np.isnat(start) or end is not None and np.isnat(end):
            raise ValueError("calculation range cannot contain NaT")
        if start is not None and end is not None and start > end:
            raise ValueError("start_time cannot be after end_time")
        if self.mode is CalculationMode.HISTORICAL and (start is None or end is None):
            raise ValueError("historical calculations require start_time and end_time")

        currency = self.base_currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("base_currency must be a three-letter alphabetic code")
        object.__setattr__(self, "valuation_time", valuation_time)
        object.__setattr__(self, "market_data_as_of", market_data_as_of)
        object.__setattr__(self, "start_time", start)
        object.__setattr__(self, "end_time", end)
        object.__setattr__(self, "base_currency", currency)
        object.__setattr__(self, "scenarios", tuple(self.scenarios))

    @classmethod
    def coerce(cls, value: CalculationContext | np.datetime64) -> CalculationContext:
        if isinstance(value, cls):
            return value
        return cls(cast(np.datetime64, value))

    def snapshot(self) -> dict[str, object]:
        return {
            "valuation_time": str(self.valuation_time),
            "market_data_as_of": str(self.market_data_as_of),
            "start_time": None if self.start_time is None else str(self.start_time),
            "end_time": None if self.end_time is None else str(self.end_time),
            "calendar": self.calendar,
            "base_currency": self.base_currency,
            "scenarios": [scenario.name for scenario in self.scenarios],
            "missing_data_policy": self.missing_data_policy.value,
            "mode": self.mode.value,
            "allow_lookahead": self.allow_lookahead,
            "provenance_reference": self.provenance_reference,
        }
