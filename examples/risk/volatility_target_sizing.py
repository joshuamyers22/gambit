"""Size forecasts to portfolio volatility, then apply risk limits explicitly."""

from __future__ import annotations

import math

import numpy as np
import polars as pl

import gambit

VALUATION_TIME = np.datetime64("2026-08-29T16:00")
CAPITAL = 1_000_000.0


def main() -> None:
    forecasts = pl.DataFrame(
        {
            "symbol": ["STOCK", "FUTURE"],
            "asset_class": ["equity", "future"],
            "raw_forecast": [1.0, -0.5],
        }
    )
    estimate = gambit.CovarianceEstimate(
        symbols=("STOCK", "FUTURE"),
        matrix=np.array([[0.04, 0.01], [0.01, 0.09]]),
        as_of=VALUATION_TIME,
        observations=252,
        annualization_factor=252.0,
    )
    context = gambit.CalculationContext(VALUATION_TIME, base_currency="USD")
    sizer = gambit.VolatilityTargetSizer(target_volatility=0.10)

    proposed = sizer.size(forecasts, estimate, context, capital=CAPITAL)
    stressed = estimate.with_volatility_stress(1.5).with_adverse_correlation_stress(
        proposed.positions, 0.50
    )
    overlay = gambit.PortfolioRiskOverlay(
        gambit.PortfolioRiskLimits(
            max_portfolio_volatility=0.10,
            max_stressed_volatility=0.12,
            max_sum_absolute_risk=0.20,
            max_leverage=2.0,
        )
    ).evaluate(proposed.positions, estimate, capital=CAPITAL, stressed_estimate=stressed)
    final = sizer.size(forecasts, estimate, context, capital=CAPITAL, overlay=overlay)

    print(final.positions)
    print("\nOverlay diagnostics")
    print(overlay.diagnostics)
    print(f"\nTarget volatility: {final.target_volatility:.2%}")
    print(f"Achieved volatility: {final.achieved_volatility:.2%}")

    assert final.positions["raw_forecast"].to_list() == [1.0, -0.5]
    assert math.isclose(final.pre_overlay_volatility, 0.10)
    assert final.achieved_volatility <= final.target_volatility
    assert final.overlay_multiplier == overlay.multiplier


if __name__ == "__main__":
    main()
