import numpy as np
import polars as pl
import pytest

from gambit.calculation import CalculationContext
from gambit.covariance_risk import CovarianceEstimate, PortfolioRiskOverlayResult
from gambit.position_sizing import VolatilityTargetSizer

TIMESTAMP = np.datetime64("2026-08-29T16:00")


def _estimate() -> CovarianceEstimate:
    return CovarianceEstimate(
        ("A", "B"),
        np.array([[0.04, 0.01], [0.01, 0.09]]),
        TIMESTAMP,
        100,
        252.0,
    )


def _forecasts() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["A", "B"],
            "asset_class": ["equity", "future"],
            "raw_forecast": [1.0, -0.5],
        }
    )


def test_sizer_hits_target_without_mutating_raw_forecasts() -> None:
    forecasts = _forecasts()
    result = VolatilityTargetSizer(0.10).size(forecasts, _estimate(), TIMESTAMP, capital=1_000_000)

    assert forecasts.columns == ["symbol", "asset_class", "raw_forecast"]
    assert result.positions["raw_forecast"].to_list() == [1.0, -0.5]
    assert result.positions["currency"].unique().to_list() == ["USD"]
    assert result.pre_overlay_volatility == pytest.approx(0.10)
    assert result.achieved_volatility == pytest.approx(0.10)
    assert result.positions["gross_exposure"].to_list() == pytest.approx(
        result.positions["net_exposure"].abs().to_list()
    )


def test_overlay_is_an_explicit_separate_sizing_multiplier() -> None:
    overlay = PortfolioRiskOverlayResult(
        0.4,
        pl.DataFrame({"constraint": ["leverage"], "value": [2.0], "limit": [0.8], "multiplier": [0.4]}),
    )
    result = VolatilityTargetSizer(0.10).size(
        _forecasts(), _estimate(), TIMESTAMP, capital=1_000_000, overlay=overlay
    )

    assert result.overlay_multiplier == 0.4
    assert result.overlay_diagnostics is not None
    assert result.overlay_diagnostics["constraint"].to_list() == ["leverage"]
    assert result.achieved_volatility == pytest.approx(0.04)
    np.testing.assert_allclose(
        result.positions["net_exposure"].to_numpy(),
        result.positions["target_net_exposure"].to_numpy() * 0.4,
    )
    assert result.positions["raw_forecast"].to_list() == [1.0, -0.5]


def test_zero_forecasts_produce_zero_exposure() -> None:
    forecasts = _forecasts().with_columns(pl.lit(0.0).alias("raw_forecast"))
    result = VolatilityTargetSizer(0.10).size(forecasts, _estimate(), TIMESTAMP, capital=1_000_000)

    assert result.positions["net_exposure"].to_list() == [0.0, 0.0]
    assert result.pre_overlay_volatility == 0.0
    assert result.achieved_volatility == 0.0


def test_sizer_rejects_lookahead_invalid_forecasts_and_overlay() -> None:
    cutoff = CalculationContext(TIMESTAMP, market_data_as_of=TIMESTAMP - np.timedelta64(1, "m"))
    with pytest.raises(ValueError, match="after the calculation cutoff"):
        VolatilityTargetSizer(0.10).size(_forecasts(), _estimate(), cutoff, capital=1_000_000)
    with pytest.raises(ValueError, match="one forecast row"):
        VolatilityTargetSizer(0.10).size(
            pl.concat([_forecasts(), _forecasts().head(1)]), _estimate(), TIMESTAMP, capital=1_000_000
        )
    with pytest.raises(ValueError, match="finite"):
        VolatilityTargetSizer(0.10).size(
            _forecasts().with_columns(pl.lit(float("nan")).alias("raw_forecast")),
            _estimate(),
            TIMESTAMP,
            capital=1_000_000,
        )
    with pytest.raises(ValueError, match="cannot be empty"):
        VolatilityTargetSizer(0.10).size(
            _forecasts().clear(), _estimate(), TIMESTAMP, capital=1_000_000
        )


def test_sizer_rejects_untranslated_or_unknown_symbols() -> None:
    mixed_currency = _forecasts().with_columns(pl.Series("currency", ["USD", "EUR"]))
    with pytest.raises(ValueError, match="base currency"):
        VolatilityTargetSizer(0.10).size(mixed_currency, _estimate(), TIMESTAMP, capital=1_000_000)
    with pytest.raises(ValueError, match="cannot be null"):
        VolatilityTargetSizer(0.10).size(
            _forecasts().with_columns(pl.Series("currency", ["USD", None])),
            _estimate(),
            TIMESTAMP,
            capital=1_000_000,
        )
    with pytest.raises(ValueError, match="missing symbols"):
        VolatilityTargetSizer(0.10).size(
            pl.DataFrame({"symbol": ["C"], "raw_forecast": [1.0]}),
            _estimate(),
            TIMESTAMP,
            capital=1_000_000,
        )
