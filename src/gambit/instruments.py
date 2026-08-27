"""Typed instrument metadata independent of any data or broker backend."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class AssetClass(str, Enum):
    UNKNOWN = "unknown"
    EQUITY = "equity"
    FUTURE = "future"
    OPTION = "option"
    FX = "fx"
    CRYPTO = "crypto"
    FIXED_INCOME = "fixed_income"
    BASKET = "basket"


class Tradability(str, Enum):
    ACTIVE = "active"
    IGNORED = "ignored"
    UNTRADEABLE = "untradeable"
    DUPLICATE = "duplicate"
    BAD = "bad"


@dataclass(frozen=True)
class InstrumentSpec:
    asset_class: AssetClass = AssetClass.UNKNOWN
    currency: str = "USD"
    tick_size: float | None = None
    exchange_calendar: str | None = None
    trading_timezone: str | None = None
    liquidity_group: str | None = None
    tradability: Tradability = Tradability.ACTIVE
    duplicate_of: str | None = None

    def __post_init__(self) -> None:
        currency = self.currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter alphabetic code")
        object.__setattr__(self, "currency", currency)
        if self.tick_size is not None and (not math.isfinite(self.tick_size) or self.tick_size <= 0):
            raise ValueError("tick_size must be finite and positive")
        if self.tradability is Tradability.DUPLICATE and not self.duplicate_of:
            raise ValueError("duplicate instruments must identify duplicate_of")
        if self.tradability is not Tradability.DUPLICATE and self.duplicate_of is not None:
            raise ValueError("duplicate_of is only valid for duplicate instruments")
