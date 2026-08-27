"""Read-only portfolio exposure, attribution, and scenario stress reports."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np
import polars as pl

from gambit.account import Account


@dataclass(frozen=True)
class StressScenario:
    """Named percentage shocks keyed by symbol, group, asset class, or ``*``."""

    name: str
    shocks: Mapping[str, float]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("scenario name must be non-empty")
        shocks = dict(self.shocks)
        if not all(math.isfinite(shock) for shock in shocks.values()):
            raise ValueError("scenario shocks must be finite")
        object.__setattr__(self, "shocks", MappingProxyType(shocks))

    def shock_for(self, symbol: str, group: str, asset_class: str) -> float:
        for key in (symbol, group, asset_class, "*"):
            if key in self.shocks:
                return self.shocks[key]
        return 0.0


@dataclass(frozen=True)
class PortfolioRiskReport:
    timestamp: np.datetime64
    exposures: pl.DataFrame
    attribution: pl.DataFrame
    scenario_results: pl.DataFrame

    def summary(self) -> pl.DataFrame:
        gross = self.exposures["gross_exposure"].sum() if self.exposures.height else 0.0
        net = self.exposures["net_exposure"].sum() if self.exposures.height else 0.0
        worst = self.scenario_results["stressed_pnl"].min() if self.scenario_results.height else 0.0
        return pl.DataFrame(
            {
                "timestamp": [self.timestamp.astype("datetime64[ns]")],
                "gross_exposure": [gross],
                "net_exposure": [net],
                "worst_scenario_pnl": [worst],
            }
        )


def account_exposures(account: Account, timestamp: np.datetime64) -> pl.DataFrame:
    """Create contract-level marked exposure from an account without mutating positions."""
    account.calc(timestamp)
    rows: list[dict[str, object]] = []
    for symbol, contract in account.contracts.items():
        position, price, *_rest = account.symbol_pnls[symbol].pnl(timestamp)
        if math.isclose(position, 0) or not math.isfinite(price):
            continue
        net_exposure = position * price * contract.multiplier
        spec = contract.instrument_spec
        rows.append(
            {
                "symbol": symbol,
                "contract_group": contract.contract_group.name,
                "asset_class": spec.asset_class.value,
                "currency": spec.currency,
                "quantity": position,
                "price": price,
                "multiplier": contract.multiplier,
                "net_exposure": net_exposure,
                "gross_exposure": abs(net_exposure),
            }
        )
    schema = {
        "symbol": pl.String,
        "contract_group": pl.String,
        "asset_class": pl.String,
        "currency": pl.String,
        "quantity": pl.Float64,
        "price": pl.Float64,
        "multiplier": pl.Float64,
        "net_exposure": pl.Float64,
        "gross_exposure": pl.Float64,
    }
    return pl.DataFrame(rows, schema=schema)


def attribute_exposure(exposures: pl.DataFrame, by: Sequence[str] = ("contract_group",)) -> pl.DataFrame:
    """Aggregate net/gross exposure and each bucket's share of portfolio gross."""
    if not by:
        raise ValueError("attribution dimensions cannot be empty")
    missing = set(by) - set(exposures.columns)
    if missing:
        raise ValueError(f"attribution columns are missing: {', '.join(sorted(missing))}")
    if not exposures.height:
        return pl.DataFrame(schema={**{column: pl.String for column in by}, "net_exposure": pl.Float64, "gross_exposure": pl.Float64, "gross_share": pl.Float64})
    total_gross = exposures["gross_exposure"].sum()
    return (
        exposures.group_by(list(by), maintain_order=True)
        .agg(pl.col("net_exposure").sum(), pl.col("gross_exposure").sum())
        .with_columns((pl.col("gross_exposure") / total_gross).alias("gross_share"))
    )


def run_stress_scenarios(exposures: pl.DataFrame, scenarios: Sequence[StressScenario]) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    exposure_rows = exposures.iter_rows(named=True)
    cached_rows = list(exposure_rows)
    for scenario in scenarios:
        stressed_pnl = 0.0
        shocked_gross = 0.0
        for row in cached_rows:
            shock = scenario.shock_for(row["symbol"], row["contract_group"], row["asset_class"])
            contribution = row["net_exposure"] * shock
            stressed_pnl += contribution
            shocked_gross += abs(contribution)
        rows.append({"scenario": scenario.name, "stressed_pnl": stressed_pnl, "shocked_gross": shocked_gross})
    return pl.DataFrame(
        rows,
        schema={"scenario": pl.String, "stressed_pnl": pl.Float64, "shocked_gross": pl.Float64},
    )


def analyze_account_risk(
    account: Account,
    timestamp: np.datetime64,
    scenarios: Sequence[StressScenario] = (),
    attribution_by: Sequence[str] = ("contract_group",),
) -> PortfolioRiskReport:
    exposures = account_exposures(account, timestamp)
    return PortfolioRiskReport(
        timestamp,
        exposures,
        attribute_exposure(exposures, attribution_by),
        run_stress_scenarios(exposures, scenarios),
    )
