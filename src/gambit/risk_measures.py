"""Typed portfolio risk calculations with a common Polars result shape."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np
import polars as pl

from gambit.calculation import CalculationContext
from gambit.risk_reporting import StressScenario


class RiskMeasure(Protocol):
    @property
    def name(self) -> str: ...

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame: ...


def _measure_rows(exposures: pl.DataFrame, name: str, values: pl.Expr) -> pl.DataFrame:
    dimensions = ["symbol", "contract_group", "asset_class", "currency"]
    return exposures.select(*dimensions, values.alias("value")).with_columns(
        pl.lit(name).alias("measure"), pl.lit(None, dtype=pl.String).alias("scenario")
    ).select(*dimensions, "measure", "scenario", "value")


@dataclass(frozen=True)
class PriceMeasure:
    name: str = "price"

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        return _measure_rows(exposures, self.name, pl.col("price"))


@dataclass(frozen=True)
class NetExposureMeasure:
    name: str = "net_exposure"

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        return _measure_rows(exposures, self.name, pl.col("net_exposure"))


@dataclass(frozen=True)
class GrossExposureMeasure:
    name: str = "gross_exposure"

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        return _measure_rows(exposures, self.name, pl.col("gross_exposure"))


@dataclass(frozen=True)
class ScenarioPnlMeasure:
    scenario: StressScenario
    name: str = "scenario_pnl"

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        values = [self.scenario.pnl_for(row) for row in exposures.iter_rows(named=True)]
        result = _measure_rows(exposures, self.name, pl.Series(values))
        return result.with_columns(pl.lit(self.scenario.name).alias("scenario"))


@dataclass(frozen=True)
class RiskResult:
    data: pl.DataFrame

    def filter(self, *, measure: str | None = None, scenario: str | None = None) -> RiskResult:
        result = self.data
        if measure is not None:
            result = result.filter(pl.col("measure") == measure)
        if scenario is not None:
            result = result.filter(pl.col("scenario") == scenario)
        return RiskResult(result)

    def aggregate(self, by: Sequence[str] = ("measure", "scenario")) -> pl.DataFrame:
        missing = set(by) - set(self.data.columns)
        if missing:
            raise ValueError(f"aggregation columns are missing: {', '.join(sorted(missing))}")
        if not by:
            return self.data.select(pl.col("value").sum())
        return self.data.group_by(list(by), maintain_order=True).agg(pl.col("value").sum())


def calculate_risk(
    exposures: pl.DataFrame,
    measures: Sequence[RiskMeasure],
    timestamp: np.datetime64 | CalculationContext,
) -> RiskResult:
    if not measures:
        raise ValueError("at least one risk measure is required")
    context = CalculationContext.coerce(timestamp)
    for measure in measures:
        measure_as_of = getattr(measure, "market_data_as_of", None)
        if (
            measure_as_of is not None
            and not context.allow_lookahead
            and np.datetime64(measure_as_of, "ns") > context.market_data_as_of
        ):
            raise ValueError(f"risk measure {measure.name} uses market data after the calculation cutoff")
    frames = [measure.calculate(exposures) for measure in measures]
    data = pl.concat(frames).with_columns(
        pl.lit(context.valuation_time).alias("timestamp"),
        pl.lit(context.market_data_as_of).alias("market_data_as_of"),
        pl.lit(context.base_currency).alias("base_currency"),
    )
    return RiskResult(
        data.select(
            "timestamp",
            "market_data_as_of",
            "base_currency",
            "symbol",
            "contract_group",
            "asset_class",
            "currency",
            "measure",
            "scenario",
            "value",
        )
    )
