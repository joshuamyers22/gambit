import numpy as np
import polars as pl
import pytest

from gambit.calculation import CalculationContext
from gambit.covariance_risk import PortfolioRiskOverlayResult
from gambit.position_sizing import VaRTargetSizer
from gambit.var_risk import FittedTailRiskModel, TailRiskMethod, TailRiskModel

TIMESTAMP = np.datetime64("2026-01-09")


def _model():
    returns = pl.DataFrame(
        {
            "timestamp": pl.date_range(
                np.datetime64("2026-01-01"), TIMESTAMP, interval="1d", eager=True
            ),
            "A": [0.01, -0.02, 0.015, -0.01, 0.005, -0.03, 0.02, -0.015, 0.01],
            "B": [-0.005, 0.01, -0.01, 0.005, 0.0, 0.015, -0.01, 0.01, -0.005],
        }
    )
    return TailRiskModel(
        lookback=9,
        min_observations=5,
        confidence=0.80,
        method=TailRiskMethod.HISTORICAL,
    ).fit(returns)


def _forecasts() -> pl.DataFrame:
    return pl.DataFrame({"symbol": ["A", "B"], "raw_forecast": [1.0, -0.5]})


def test_var_sizer_hits_target_and_preserves_forecast() -> None:
    result = VaRTargetSizer(0.02).size(_forecasts(), _model(), TIMESTAMP, capital=1_000_000)

    assert result.pre_overlay_var == pytest.approx(0.02)
    assert result.achieved_var == pytest.approx(0.02)
    assert result.positions["raw_forecast"].to_list() == [1.0, -0.5]
    assert result.positions["currency"].unique().to_list() == ["USD"]


def test_var_sizer_applies_overlay_as_separate_multiplier() -> None:
    diagnostics = pl.DataFrame(
        {"constraint": ["leverage"], "value": [2.0], "limit": [1.0], "multiplier": [0.5]}
    )
    overlay = PortfolioRiskOverlayResult(0.5, diagnostics)
    result = VaRTargetSizer(0.02).size(
        _forecasts(), _model(), TIMESTAMP, capital=1_000_000, overlay=overlay
    )

    assert result.pre_overlay_var == pytest.approx(0.02)
    assert result.achieved_var == pytest.approx(0.01)
    np.testing.assert_allclose(
        result.positions["net_exposure"].to_numpy(),
        result.positions["target_net_exposure"].to_numpy() * 0.5,
    )
    assert result.overlay_diagnostics is not None


def test_var_sizer_rejects_future_model_and_unknown_symbol() -> None:
    context = CalculationContext(TIMESTAMP, market_data_as_of=TIMESTAMP - np.timedelta64(1, "D"))
    with pytest.raises(ValueError, match="after the calculation cutoff"):
        VaRTargetSizer(0.02).size(_forecasts(), _model(), context, capital=1_000_000)
    with pytest.raises(ValueError, match="missing symbols"):
        VaRTargetSizer(0.02).size(
            pl.DataFrame({"symbol": ["C"], "raw_forecast": [1.0]}),
            _model(),
            TIMESTAMP,
            capital=1_000_000,
        )


def test_nonzero_forecasts_reject_zero_modeled_var() -> None:
    zero_risk = FittedTailRiskModel(
        ("A", "B"),
        np.zeros((3, 2)),
        TIMESTAMP,
        0.80,
        1,
        TailRiskMethod.HISTORICAL,
    )

    with pytest.raises(ValueError, match="zero modeled value at risk"):
        VaRTargetSizer(0.02).size(_forecasts(), zero_risk, TIMESTAMP, capital=1_000_000)
