"""Estimate portfolio tail risk and size forecasts to a VaR target."""

from __future__ import annotations

import numpy as np
import polars as pl

import gambit

VALUATION_TIME = np.datetime64("2026-01-09")
CAPITAL = 1_000_000.0


def main() -> None:
    returns = pl.DataFrame(
        {
            "timestamp": pl.date_range(
                np.datetime64("2026-01-01"), VALUATION_TIME, interval="1d", eager=True
            ),
            "STOCK": [0.01, -0.02, 0.015, -0.01, 0.005, -0.03, 0.02, -0.015, 0.01],
            "FUTURE": [-0.005, 0.01, -0.01, 0.005, 0.0, 0.015, -0.01, 0.01, -0.005],
        }
    )
    context = gambit.CalculationContext(VALUATION_TIME, base_currency="USD")
    historical = gambit.TailRiskModel(
        lookback=9,
        min_observations=5,
        confidence=0.80,
        method=gambit.TailRiskMethod.HISTORICAL,
    ).fit(returns, as_of=context.market_data_as_of)
    gaussian = gambit.TailRiskModel(
        lookback=9,
        min_observations=5,
        confidence=0.80,
        method=gambit.TailRiskMethod.GAUSSIAN,
    ).fit(returns, as_of=context.market_data_as_of)
    forecasts = pl.DataFrame(
        {"symbol": ["STOCK", "FUTURE"], "raw_forecast": [1.0, -0.5]}
    )
    sized = gambit.VaRTargetSizer(target_var=0.02).size(
        forecasts,
        historical,
        context,
        capital=CAPITAL,
    )
    risk = gambit.calculate_risk(
        sized.positions.with_columns(
            pl.lit("portfolio").alias("contract_group"),
            pl.lit("mixed").alias("asset_class"),
            pl.lit(1.0).alias("price"),
        ),
        [
            gambit.PortfolioVaRMeasure(historical),
            gambit.PortfolioExpectedShortfallMeasure(historical),
            gambit.PortfolioVaRMeasure(gaussian, name="gaussian_value_at_risk"),
        ],
        context,
    )

    print(sized.positions)
    print("\nTail-risk measures")
    print(risk.aggregate())
    print(f"\nHistorical VaR target: {sized.target_var:.2%}")
    print(f"Achieved historical VaR: {sized.achieved_var:.2%}")

    assert sized.positions["raw_forecast"].to_list() == [1.0, -0.5]
    assert np.isclose(sized.achieved_var, 0.02)
    assert risk.filter(measure="expected_shortfall").data["value"][0] >= 20_000.0


if __name__ == "__main__":
    main()
