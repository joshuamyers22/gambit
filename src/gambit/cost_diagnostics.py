"""Reconciled period, instrument, and rule cost/turnover diagnostics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
import polars as pl


class CostPeriod(str, Enum):
    """Supported deterministic calendar buckets for cost diagnostics."""

    DAILY = "1d"
    WEEKLY = "1w"
    MONTHLY = "1mo"


def _identities(frame: pl.DataFrame, *, owner: str) -> pl.DataFrame:
    if not isinstance(frame, pl.DataFrame):
        raise TypeError(f"{owner} must be a Polars DataFrame")
    required = {"timestamp", "symbol", "rule"}
    if missing := required - set(frame.columns):
        raise ValueError(f"{owner} columns are missing: {', '.join(sorted(missing))}")
    dtype = frame.schema["timestamp"]
    if dtype != pl.Date and not isinstance(dtype, pl.Datetime):
        raise TypeError(f"{owner} timestamp must be Polars Date or Datetime")
    if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
        raise ValueError(f"{owner} timestamps must be timezone-naive")
    normalized = frame.with_columns(
        pl.col("timestamp").cast(pl.Datetime("ns")),
        pl.col("symbol").cast(pl.String),
        pl.col("rule").cast(pl.String),
    )
    if normalized.select(pl.any_horizontal(pl.col("timestamp", "symbol", "rule").is_null()).any()).item():
        raise ValueError(f"{owner} timestamp, symbol, and rule cannot be null")
    if normalized.select(
        ((pl.col("symbol").str.len_chars() == 0) | (pl.col("rule").str.len_chars() == 0)).any()
    ).item():
        raise ValueError(f"{owner} symbol and rule cannot be empty")
    return normalized


def _finite_columns(frame: pl.DataFrame, columns: tuple[str, ...], *, owner: str) -> pl.DataFrame:
    if missing := set(columns) - set(frame.columns):
        raise ValueError(f"{owner} columns are missing: {', '.join(sorted(missing))}")
    if any(not frame.schema[column].is_numeric() or frame.schema[column] == pl.Boolean for column in columns):
        raise TypeError(f"{owner} numeric columns must have numeric non-boolean types")
    normalized = frame.with_columns(*(pl.col(column).cast(pl.Float64) for column in columns))
    if normalized.select(pl.any_horizontal(pl.col(*columns).is_null() | ~pl.col(*columns).is_finite()).any()).item():
        raise ValueError(f"{owner} numeric values must be finite and non-null")
    return normalized


@dataclass(frozen=True, init=False)
class CostTurnoverReport:
    """Detached reconciled cost and turnover rows."""

    _data: pl.DataFrame

    def __init__(self, data: pl.DataFrame) -> None:
        object.__setattr__(self, "_data", data.clone())

    @property
    def data(self) -> pl.DataFrame:
        return self._data.clone()


@dataclass(frozen=True)
class CostTurnoverAnalyzer:
    """Aggregate explicitly attributed incremental P&L and executed trades."""

    capital: float
    period: CostPeriod = CostPeriod.MONTHLY
    reconciliation_tolerance: float = 1e-10

    def __post_init__(self) -> None:
        for name, value, positive in (
            ("capital", self.capital, True),
            ("reconciliation tolerance", self.reconciliation_tolerance, False),
        ):
            if (
                type(value) is bool
                or not isinstance(value, (int, float, np.integer, np.floating))
                or not math.isfinite(value)
                or (value <= 0 if positive else value < 0)
            ):
                qualifier = "positive" if positive else "non-negative"
                raise ValueError(f"cost diagnostic {name} must be finite and {qualifier}")
        if not isinstance(self.period, CostPeriod):
            raise TypeError("cost diagnostic period must be CostPeriod")
        object.__setattr__(self, "capital", float(self.capital))
        object.__setattr__(self, "reconciliation_tolerance", float(self.reconciliation_tolerance))

    def analyze(self, pnl: pl.DataFrame, trades: pl.DataFrame) -> CostTurnoverReport:
        """Return reconciled period/instrument/rule rows without estimating fills."""
        pnl_values = _finite_columns(
            _identities(pnl, owner="cost diagnostic P&L"),
            ("gross_pnl", "net_pnl"),
            owner="cost diagnostic P&L",
        ).select("timestamp", "symbol", "rule", "gross_pnl", "net_pnl")
        trade_values = _finite_columns(
            _identities(trades, owner="cost diagnostic trades"),
            ("qty", "price", "multiplier", "fee", "commission"),
            owner="cost diagnostic trades",
        ).select("timestamp", "symbol", "rule", "qty", "price", "multiplier", "fee", "commission")
        if pnl_values.is_empty():
            raise ValueError("cost diagnostic P&L cannot be empty")
        if trade_values.select(((pl.col("qty") == 0) | (pl.col("multiplier") <= 0)).any()).item():
            raise ValueError("cost diagnostic trades require nonzero quantity and positive multiplier")

        keys = ["period_start", "symbol", "rule"]
        pnl_by_period = (
            pnl_values.with_columns(pl.col("timestamp").dt.truncate(self.period.value).alias("period_start"))
            .group_by(*keys)
            .agg(
                pl.col("gross_pnl").sum(),
                pl.col("net_pnl").sum(),
            )
        )
        trades_by_period = (
            trade_values.with_columns(
                pl.col("timestamp").dt.truncate(self.period.value).alias("period_start"),
                (pl.col("qty") * pl.col("price") * pl.col("multiplier")).abs().alias("traded_notional"),
            )
            .group_by(*keys)
            .agg(
                pl.col("traded_notional").sum(),
                pl.col("fee").sum(),
                pl.col("commission").sum(),
                pl.len().cast(pl.UInt32).alias("trade_count"),
            )
        )
        joined = pnl_by_period.join(trades_by_period, on=keys, how="full", coalesce=True).with_columns(
            pl.col("gross_pnl", "net_pnl", "traded_notional", "fee", "commission").fill_null(0.0),
            pl.col("trade_count").fill_null(0),
        ).sort(*keys)
        gross = joined["gross_pnl"].to_numpy()
        net = joined["net_pnl"].to_numpy()
        explicit_cost = joined["fee"].to_numpy() + joined["commission"].to_numpy()
        if not np.allclose(
            gross - net,
            explicit_cost,
            rtol=0.0,
            atol=self.reconciliation_tolerance,
        ):
            raise ValueError("cost diagnostic gross/net P&L does not reconcile to fees and commission")
        report = joined.with_columns(
            pl.lit(self.period.name.lower()).alias("period"),
            pl.lit(self.capital).alias("capital"),
            (pl.col("fee") + pl.col("commission")).alias("explicit_cost"),
            (pl.col("traded_notional") / self.capital).alias("turnover"),
            (pl.col("gross_pnl") / self.capital).alias("gross_return"),
            (pl.col("net_pnl") / self.capital).alias("net_return"),
            ((pl.col("gross_pnl") - pl.col("net_pnl")) / self.capital).alias("explicit_cost_drag"),
        ).select(
            "period_start",
            "period",
            "symbol",
            "rule",
            "capital",
            "gross_pnl",
            "net_pnl",
            "fee",
            "commission",
            "explicit_cost",
            "traded_notional",
            "turnover",
            "gross_return",
            "net_return",
            "explicit_cost_drag",
            "trade_count",
        )
        if report.select(
            pl.any_horizontal(
                pl.col(
                    "gross_pnl",
                    "net_pnl",
                    "fee",
                    "commission",
                    "explicit_cost",
                    "traded_notional",
                    "turnover",
                    "gross_return",
                    "net_return",
                    "explicit_cost_drag",
                ).is_nan()
                | pl.col(
                    "gross_pnl",
                    "net_pnl",
                    "fee",
                    "commission",
                    "explicit_cost",
                    "traded_notional",
                    "turnover",
                    "gross_return",
                    "net_return",
                    "explicit_cost_drag",
                ).is_infinite()
            ).any()
        ).item():
            raise ValueError("cost diagnostic aggregation produced non-finite output")
        return CostTurnoverReport(report)
