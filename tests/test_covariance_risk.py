import numpy as np
import polars as pl
import pytest

from gambit.calculation import CalculationContext
from gambit.covariance_risk import (
    ComponentVolatilityMeasure,
    CovarianceEstimate,
    CovarianceRiskModel,
    DiversificationRatioMeasure,
    PortfolioRiskLimits,
    PortfolioRiskOverlay,
    PortfolioVolatilityMeasure,
)
from gambit.risk_measures import calculate_risk

TIMESTAMP = np.datetime64("2026-01-09")


def _returns() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": pl.date_range(
                np.datetime64("2026-01-01"), np.datetime64("2026-01-10"), interval="1d", eager=True
            ),
            "A": [0.01, -0.01, 0.02, -0.02, 0.01, 0.00, 0.015, -0.005, 0.01, 0.50],
            "B": [0.005, -0.005, 0.01, -0.01, 0.005, 0.00, 0.0075, -0.0025, 0.005, -0.50],
        }
    )


def _exposures() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["A", "B"],
            "contract_group": ["equity", "future"],
            "asset_class": ["equity", "future"],
            "currency": ["USD", "USD"],
            "net_exposure": [100_000.0, -200_000.0],
            "gross_exposure": [100_000.0, 200_000.0],
            "price": [100.0, 200.0],
        }
    )


def test_covariance_model_is_point_in_time_and_immutable() -> None:
    model = CovarianceRiskModel(lookback=9, min_observations=5, annualization_factor=252, diagonal_shrinkage=0.1)
    estimate = model.fit(_returns(), as_of=TIMESTAMP)
    without_future_row = model.fit(_returns().head(9), as_of=TIMESTAMP)

    np.testing.assert_allclose(estimate.matrix, without_future_row.matrix)
    assert estimate.observations == 9
    assert estimate.as_of == TIMESTAMP.astype("datetime64[ns]")
    with pytest.raises(ValueError):
        estimate.matrix[0, 0] = 0.0


def test_covariance_stresses_preserve_invariants() -> None:
    estimate = CovarianceEstimate(
        ("A", "B"),
        np.array([[0.04, -0.01], [-0.01, 0.09]]),
        TIMESTAMP,
        100,
        252,
    )

    volatility_stress = estimate.with_volatility_stress(1.5)
    correlation_stress = estimate.with_correlation_stress(1.0)
    adverse_stress = estimate.with_adverse_correlation_stress(_exposures(), 1.0)

    np.testing.assert_allclose(volatility_stress.volatilities, estimate.volatilities * 1.5)
    np.testing.assert_allclose(correlation_stress.correlation, np.ones((2, 2)))
    np.testing.assert_allclose(correlation_stress.volatilities, estimate.volatilities)
    assert adverse_stress.portfolio_volatility(_exposures()) >= estimate.portfolio_volatility(_exposures())


def test_component_risk_adds_to_portfolio_volatility() -> None:
    estimate = CovarianceEstimate(
        ("A", "B"),
        np.array([[0.04, 0.018], [0.018, 0.09]]),
        TIMESTAMP,
        100,
        252,
    )
    result = calculate_risk(
        _exposures(),
        [PortfolioVolatilityMeasure(estimate), ComponentVolatilityMeasure(estimate), DiversificationRatioMeasure(estimate)],
        TIMESTAMP,
    )

    total = result.filter(measure="portfolio_volatility").data["value"][0]
    components = result.filter(measure="component_volatility").aggregate(by=())["value"][0]
    ratio = result.filter(measure="diversification_ratio").data["value"][0]
    assert components == pytest.approx(total)
    assert ratio >= 1.0


def test_risk_overlay_uses_the_most_conservative_constraint() -> None:
    estimate = CovarianceEstimate(
        ("A", "B"),
        np.array([[0.04, 0.018], [0.018, 0.09]]),
        TIMESTAMP,
        100,
        252,
    )
    limits = PortfolioRiskLimits(
        max_portfolio_volatility=0.10,
        max_stressed_volatility=0.12,
        max_sum_absolute_risk=0.20,
        max_leverage=0.25,
    )
    result = PortfolioRiskOverlay(limits).evaluate(
        _exposures(), estimate, capital=1_000_000, stressed_estimate=estimate.with_correlation_stress(0.5)
    )

    assert result.multiplier == pytest.approx(0.25 / 0.30)
    assert result.diagnostics.sort("multiplier")["constraint"][0] == "leverage"


def test_covariance_risk_rejects_missing_symbols_and_mixed_currencies() -> None:
    estimate = CovarianceEstimate(("A",), np.array([[0.04]]), TIMESTAMP, 100, 252)
    with pytest.raises(ValueError, match="missing symbols"):
        estimate.portfolio_volatility(_exposures())

    mixed = _exposures().with_columns(
        pl.when(pl.col("symbol") == "B").then(pl.lit("EUR")).otherwise(pl.col("currency")).alias("currency")
    )
    complete = CovarianceEstimate(("A", "B"), np.eye(2), TIMESTAMP, 100, 252)
    with pytest.raises(ValueError, match="translated to one currency"):
        PortfolioVolatilityMeasure(complete).calculate(mixed)

    duplicated = pl.concat([_exposures(), _exposures().head(1)])
    with pytest.raises(ValueError, match="one exposure row per symbol"):
        ComponentVolatilityMeasure(complete).calculate(duplicated)


def test_risk_measure_rejects_covariance_after_market_data_cutoff() -> None:
    estimate = CovarianceEstimate(("A", "B"), np.eye(2), TIMESTAMP, 100, 252)
    context = CalculationContext(
        valuation_time=TIMESTAMP,
        market_data_as_of=TIMESTAMP - np.timedelta64(1, "D"),
    )

    with pytest.raises(ValueError, match="after the calculation cutoff"):
        calculate_risk(_exposures(), [PortfolioVolatilityMeasure(estimate)], context)
