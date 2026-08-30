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


def _measure_rows(exposures: pl.DataFrame, name: str, values: pl.Expr, unit: str | pl.Expr) -> pl.DataFrame:
    dimensions = ["symbol", "contract_group", "asset_class", "currency"]
    unit_expression = unit if isinstance(unit, pl.Expr) else pl.lit(unit)
    return exposures.select(*dimensions, values.alias("value")).with_columns(
        pl.lit(name).alias("measure"),
        pl.lit(None, dtype=pl.String).alias("scenario"),
        unit_expression.alias("unit"),
    ).select(*dimensions, "measure", "scenario", "unit", "value")


@dataclass(frozen=True)
class PriceMeasure:
    name: str = "price"

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        return _measure_rows(exposures, self.name, pl.col("price"), "market_price")


@dataclass(frozen=True)
class NetExposureMeasure:
    name: str = "net_exposure"

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        return _measure_rows(exposures, self.name, pl.col("net_exposure"), pl.col("currency"))


@dataclass(frozen=True)
class GrossExposureMeasure:
    name: str = "gross_exposure"

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        return _measure_rows(exposures, self.name, pl.col("gross_exposure"), pl.col("currency"))


@dataclass(frozen=True)
class ScenarioPnlMeasure:
    scenario: StressScenario
    name: str = "scenario_pnl"

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        values = [self.scenario.pnl_for(row) for row in exposures.iter_rows(named=True)]
        result = _measure_rows(exposures, self.name, pl.Series(values), pl.col("currency"))
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

    def aggregate(self, by: Sequence[str] = ("measure", "scenario", "unit")) -> pl.DataFrame:
        missing = set(by) - set(self.data.columns)
        if missing:
            raise ValueError(f"aggregation columns are missing: {', '.join(sorted(missing))}")
        if not by:
            units = self.data["unit"].unique().to_list()
            measures = self.data["measure"].unique().to_list()
            scenarios = self.data["scenario"].unique().to_list()
            if len(units) != 1 or len(measures) != 1 or len(scenarios) != 1:
                raise ValueError("unqualified totals require one measure, scenario, and unit")
            return self.data.select(pl.col("unit").first(), pl.col("value").sum())
        grouping = list(by)
        for boundary in ("measure", "scenario", "unit"):
            if boundary not in grouping:
                grouping.append(boundary)
        return self.data.group_by(grouping, maintain_order=True).agg(pl.col("value").sum())


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
            "unit",
            "value",
        )
    )
