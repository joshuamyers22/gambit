import numpy as np
import polars as pl
import pytest

from gambit.risk_measures import GrossExposureMeasure, NetExposureMeasure, ScenarioPnlMeasure, calculate_risk
from gambit.risk_reporting import MarketDataPattern, MarketDataShock, ShockType, StressScenario

TIMESTAMP = np.datetime64("2024-01-02")


def _exposures():
    return pl.DataFrame(
        {
            "symbol": ["STOCK", "FUTURE"],
            "contract_group": ["equities", "futures"],
            "asset_class": ["equity", "future"],
            "currency": ["USD", "USD"],
            "quantity": [2.0, -3.0],
            "price": [100.0, 50.0],
            "multiplier": [1.0, 10.0],
            "net_exposure": [200.0, -1500.0],
            "gross_exposure": [200.0, 1500.0],
        }
    )


def test_typed_measures_share_a_long_form_result() -> None:
    result = calculate_risk(_exposures(), [NetExposureMeasure(), GrossExposureMeasure()], TIMESTAMP)

    assert result.data.shape == (4, 8)
    assert result.filter(measure="net_exposure").aggregate()["value"][0] == -1300.0
    assert result.filter(measure="gross_exposure").aggregate(by=())["value"][0] == 1700.0


def test_pattern_scenario_combines_absolute_and_relative_shocks() -> None:
    scenario = StressScenario(
        "combined",
        market_shocks=(
            MarketDataShock(MarketDataPattern(asset_class="equity"), -10.0, ShockType.ABSOLUTE),
            MarketDataShock(MarketDataPattern(currency="USD"), -0.1),
        ),
    )

    result = calculate_risk(_exposures(), [ScenarioPnlMeasure(scenario)], TIMESTAMP)

    # STOCK: (100 - 10) * .9 = 81, pnl = 2 * -19 = -38
    # FUTURE: 50 * .9 = 45, pnl = -3 * 10 * -5 = 150
    assert result.aggregate()["value"][0] == pytest.approx(112.0)
    assert result.data["scenario"].unique().to_list() == ["combined"]


def test_market_pattern_requires_a_dimension() -> None:
    with pytest.raises(ValueError, match="at least one"):
        MarketDataPattern()
