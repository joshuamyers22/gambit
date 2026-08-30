import numpy as np
import polars as pl
import pytest
from scipy.stats import norm

from gambit.calculation import CalculationContext
from gambit.risk_measures import calculate_risk
from gambit.var_risk import (
    PortfolioExpectedShortfallMeasure,
    PortfolioVaRMeasure,
    TailRiskMethod,
    TailRiskModel,
)

TIMESTAMP = np.datetime64("2026-01-09")


def _returns() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": pl.date_range(
                np.datetime64("2026-01-01"), np.datetime64("2026-01-10"), interval="1d", eager=True
            ),
            "A": [0.01, -0.02, 0.015, -0.01, 0.005, -0.03, 0.02, -0.015, 0.01, -0.90],
            "B": [-0.005, 0.01, -0.01, 0.005, 0.0, 0.015, -0.01, 0.01, -0.005, 0.90],
        }
    )


def _exposures() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["A", "B"],
            "contract_group": ["equity", "rates"],
            "asset_class": ["equity", "future"],
            "currency": ["USD", "USD"],
            "net_exposure": [100_000.0, -50_000.0],
            "gross_exposure": [100_000.0, 50_000.0],
            "price": [100.0, 100.0],
        }
    )


def test_historical_tail_risk_is_point_in_time_and_matches_empirical_loss() -> None:
    model = TailRiskModel(
        lookback=9,
        min_observations=5,
        confidence=0.80,
        method=TailRiskMethod.HISTORICAL,
    ).fit(_returns(), as_of=TIMESTAMP)
    estimate = model.evaluate(_exposures())
    values = _returns().head(9).select("A", "B").to_numpy()
    losses = -(values @ np.array([100_000.0, -50_000.0]))
    expected_var = np.quantile(losses, 0.80, method="higher")

    assert model.as_of == TIMESTAMP.astype("datetime64[ns]")
    assert estimate.value_at_risk == expected_var
    assert estimate.expected_shortfall == pytest.approx(losses[losses >= expected_var].mean())
    assert estimate.expected_shortfall >= estimate.value_at_risk >= 0
    with pytest.raises(ValueError):
        model.returns[0, 0] = 0.0


def test_historical_horizon_uses_overlapping_aggregated_pnl() -> None:
    model = TailRiskModel(
        lookback=9,
        min_observations=5,
        confidence=0.75,
        horizon_days=3,
        method=TailRiskMethod.HISTORICAL,
    ).fit(_returns(), as_of=TIMESTAMP)

    estimate = model.evaluate(_exposures())

    assert estimate.observations == 7
    assert estimate.horizon_days == 3


def test_gaussian_var_and_expected_shortfall_follow_declared_formula() -> None:
    model = TailRiskModel(
        lookback=9,
        min_observations=5,
        confidence=0.95,
        horizon_days=2,
        method=TailRiskMethod.GAUSSIAN,
    ).fit(_returns(), as_of=TIMESTAMP)
    estimate = model.evaluate(_exposures())
    pnl = _returns().head(9).select("A", "B").to_numpy() @ np.array([100_000.0, -50_000.0])
    mean_loss = -pnl.mean() * 2
    sigma = pnl.std(ddof=1) * np.sqrt(2)
    z_score = norm.ppf(0.95)

    assert estimate.value_at_risk == pytest.approx(max(mean_loss + z_score * sigma, 0.0))
    assert estimate.expected_shortfall == pytest.approx(
        max(mean_loss + sigma * norm.pdf(z_score) / 0.05, estimate.value_at_risk)
    )


def test_tail_risk_measures_are_unit_safe_and_reject_lookahead() -> None:
    model = TailRiskModel(lookback=9, min_observations=5, confidence=0.80).fit(
        _returns(), as_of=TIMESTAMP
    )
    result = calculate_risk(
        _exposures(),
        [PortfolioVaRMeasure(model), PortfolioExpectedShortfallMeasure(model)],
        TIMESTAMP,
    )

    assert result.data["unit"].unique().to_list() == ["USD"]
    assert result.filter(measure="expected_shortfall").data["value"][0] >= result.filter(
        measure="value_at_risk"
    ).data["value"][0]
    context = CalculationContext(TIMESTAMP, market_data_as_of=TIMESTAMP - np.timedelta64(1, "D"))
    with pytest.raises(ValueError, match="after the calculation cutoff"):
        calculate_risk(_exposures(), [PortfolioVaRMeasure(model)], context)


def test_tail_model_rejects_mixed_currency_missing_symbols_and_bad_sample() -> None:
    model = TailRiskModel(lookback=9, min_observations=5).fit(_returns(), as_of=TIMESTAMP)
    mixed = _exposures().with_columns(pl.Series("currency", ["USD", "EUR"]))
    with pytest.raises(ValueError, match="translated to one currency"):
        model.evaluate(mixed)
    with pytest.raises(ValueError, match="missing symbols"):
        model.evaluate(
            pl.DataFrame({"symbol": ["C"], "currency": ["USD"], "net_exposure": [1.0]})
        )
    with pytest.raises(ValueError, match="complete observations"):
        TailRiskModel(lookback=9, min_observations=9).fit(_returns().head(4), as_of=TIMESTAMP)
    duplicate = pl.concat([_returns().head(2), _returns().head(1)])
    with pytest.raises(ValueError, match="timestamps must be unique"):
        TailRiskModel(lookback=9, min_observations=2).fit(duplicate, as_of=TIMESTAMP)


def test_tail_model_as_of_tracks_last_finite_observation() -> None:
    returns = _returns().head(9).with_columns(
        pl.when(pl.col("timestamp") == TIMESTAMP)
        .then(float("inf"))
        .otherwise(pl.col("A"))
        .alias("A")
    )

    model = TailRiskModel(lookback=9, min_observations=5).fit(returns, as_of=TIMESTAMP)

    assert model.as_of == (TIMESTAMP - np.timedelta64(1, "D")).astype("datetime64[ns]")


def test_tail_model_normalizes_method_and_rejects_fractional_horizon() -> None:
    model = TailRiskModel(lookback=9, min_observations=5, method="historical")  # type: ignore[arg-type]

    assert model.method is TailRiskMethod.HISTORICAL
    with pytest.raises(ValueError, match="positive integer"):
        TailRiskModel(lookback=9, min_observations=5, horizon_days=1.5)  # type: ignore[arg-type]
