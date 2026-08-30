"""Estimate portfolio covariance risk and derive a conservative scale factor."""

from __future__ import annotations

import math

import numpy as np
import polars as pl
from common import VALUATION_TIME, build_demo_account

import gambit


def synthetic_returns() -> pl.DataFrame:
    """Create reproducible correlated returns ending at the valuation date."""
    rng = np.random.default_rng(20260829)
    observations = 300
    market = rng.normal(0.0, 0.008, observations)
    equity = market + rng.normal(0.0, 0.006, observations)
    future = 0.7 * market + rng.normal(0.0, 0.005, observations)
    end = VALUATION_TIME.astype("datetime64[D]") + np.timedelta64(1, "D")
    timestamps = np.arange(end - np.timedelta64(observations, "D"), end)
    return pl.DataFrame({"timestamp": timestamps, "ACME": equity, "INDEX-FUT": future})


def main() -> None:
    account, _contracts = build_demo_account()
    exposures = gambit.account_exposures(account, VALUATION_TIME)
    estimate = gambit.CovarianceRiskModel(
        lookback=252,
        min_observations=120,
        diagonal_shrinkage=0.10,
    ).fit(synthetic_returns(), as_of=VALUATION_TIME)

    measures = gambit.calculate_risk(
        exposures,
        (
            gambit.PortfolioVolatilityMeasure(estimate),
            gambit.ComponentVolatilityMeasure(estimate),
            gambit.DiversificationRatioMeasure(estimate),
        ),
        VALUATION_TIME,
    )
    stressed = estimate.with_volatility_stress(1.5).with_adverse_correlation_stress(exposures, 0.50)
    overlay = gambit.PortfolioRiskOverlay(
        gambit.PortfolioRiskLimits(
            max_portfolio_volatility=0.10,
            max_stressed_volatility=0.12,
            max_sum_absolute_risk=0.20,
            max_leverage=0.50,
        )
    ).evaluate(exposures, estimate, capital=1_000_000.0, stressed_estimate=stressed)

    print(measures.aggregate())
    print("\nRisk-overlay diagnostics")
    print(overlay.diagnostics)
    print(f"\nPosition multiplier: {overlay.multiplier:.4f}")

    component_total = measures.filter(measure="component_volatility").aggregate(by=())["value"][0]
    portfolio_total = measures.filter(measure="portfolio_volatility").data["value"][0]
    assert math.isclose(component_total, portfolio_total)
    assert math.isclose(overlay.multiplier, 0.50 / 0.60)


if __name__ == "__main__":
    main()
