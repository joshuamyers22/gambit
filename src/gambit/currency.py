"""Explicit point-in-time currency translation for portfolio risk inputs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import numpy as np
import polars as pl

from gambit.calculation import CalculationContext


def _currency_code(value: str, *, field: str) -> str:
    code = value.upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError(f"{field} must be a three-letter alphabetic code")
    return code


@dataclass(frozen=True)
class FxRateSnapshot:
    """Base-currency value of one unit of each quoted currency at ``as_of``."""

    base_currency: str
    as_of: np.datetime64
    rates: Mapping[str, float]
    source: str | None = None

    def __post_init__(self) -> None:
        base_currency = _currency_code(self.base_currency, field="base_currency")
        as_of: np.datetime64 = np.datetime64(str(self.as_of)).astype("datetime64[ns]")
        if np.isnat(as_of):
            raise ValueError("FX as_of cannot be NaT")
        normalized: dict[str, float] = {}
        for currency, rate in self.rates.items():
            code = _currency_code(currency, field="FX currency")
            if code in normalized:
                raise ValueError(f"duplicate FX currency after normalization: {code}")
            value = float(rate)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"FX rate for {code} must be finite and positive")
            normalized[code] = value
        if base_currency in normalized and not math.isclose(normalized[base_currency], 1.0):
            raise ValueError("base-currency FX rate must equal one")
        normalized[base_currency] = 1.0
        object.__setattr__(self, "base_currency", base_currency)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "rates", MappingProxyType(normalized))

    def rate(self, currency: str) -> float:
        code = _currency_code(currency, field="currency")
        try:
            return self.rates[code]
        except KeyError as exc:
            raise ValueError(f"FX rate is missing for {code}") from exc


def translate_exposures(
    exposures: pl.DataFrame,
    snapshot: FxRateSnapshot,
    context: CalculationContext | np.datetime64,
) -> pl.DataFrame:
    """Translate marked monetary columns while retaining their local values."""
    calculation = CalculationContext.coerce(context)
    if not calculation.allow_lookahead and snapshot.as_of > calculation.market_data_as_of:
        raise ValueError("FX snapshot uses market data after the calculation cutoff")
    if snapshot.base_currency != calculation.base_currency:
        raise ValueError("FX snapshot and calculation context base currencies must match")

    monetary_columns = ("price", "net_exposure", "gross_exposure")
    required = {"currency", *monetary_columns}
    if missing := required - set(exposures.columns):
        raise ValueError(f"exposure columns are missing: {', '.join(sorted(missing))}")
    if exposures["currency"].null_count():
        raise ValueError("exposure currency cannot be null")
    currencies = exposures["currency"].drop_nulls().unique().to_list()
    missing_rates = sorted(str(currency) for currency in currencies if str(currency).upper() not in snapshot.rates)
    if missing_rates:
        raise ValueError(f"FX rates are missing for: {', '.join(missing_rates)}")

    rate_by_currency = dict(snapshot.rates)
    translated = exposures.with_columns(
        pl.col("currency").alias("local_currency"),
        *(pl.col(column).alias(f"local_{column}") for column in monetary_columns),
    ).with_columns(
        pl.col("currency")
        .str.to_uppercase()
        .replace_strict(rate_by_currency, return_dtype=pl.Float64)
        .alias("fx_rate")
    )
    return translated.with_columns(
        pl.lit(snapshot.base_currency).alias("currency"),
        *((pl.col(column) * pl.col("fx_rate")).alias(column) for column in monetary_columns),
        pl.lit(snapshot.as_of).alias("fx_as_of"),
        pl.lit(snapshot.source, dtype=pl.String).alias("fx_source"),
    )
