from types import SimpleNamespace

import numpy as np
import pytest

from gambit.account import Account
from gambit.instruments import AssetClass, InstrumentSpec
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Trade
from gambit.risk_reporting import StressScenario, analyze_account_risk

TIMESTAMP = np.datetime64("2024-01-02T16:00")


def _price(contract, _timestamps, _index, context):
    return context.prices[contract.symbol]


def _account():
    equity_group = ContractGroup.get("equities")
    future_group = ContractGroup.get("futures")
    stock = Contract.create("STOCK", equity_group, instrument_spec=InstrumentSpec(asset_class=AssetClass.EQUITY))
    future = Contract.create(
        "FUTURE",
        future_group,
        multiplier=10,
        instrument_spec=InstrumentSpec(asset_class=AssetClass.FUTURE),
    )
    context = SimpleNamespace(prices={"STOCK": 100.0, "FUTURE": 50.0})
    account = Account([equity_group, future_group], np.array([TIMESTAMP]), _price, context)
    account.add_trades(
        [
            Trade(stock, MarketOrder(contract=stock, timestamp=TIMESTAMP, qty=2), TIMESTAMP, 2, 100.0),
            Trade(future, MarketOrder(contract=future, timestamp=TIMESTAMP, qty=-3), TIMESTAMP, -3, 50.0),
        ]
    )
    return account


def test_risk_report_attributes_gross_and_net_exposure() -> None:
    report = analyze_account_risk(_account(), TIMESTAMP)

    assert report.exposures["net_exposure"].to_list() == [200.0, -1500.0]
    assert report.attribution["gross_exposure"].sum() == 1700.0
    assert report.attribution["gross_share"].sum() == pytest.approx(1.0)
    assert report.summary()["net_exposure"][0] == -1300.0


def test_stress_scenarios_match_symbol_before_asset_class_and_default() -> None:
    scenarios = [
        StressScenario("risk-off", {"equity": -0.1, "FUTURE": 0.05}),
        StressScenario("all-down", {"*": -0.02}),
    ]

    results = analyze_account_risk(_account(), TIMESTAMP, scenarios).scenario_results

    assert results.filter(results["scenario"] == "risk-off")["stressed_pnl"][0] == pytest.approx(-95.0)
    assert results.filter(results["scenario"] == "all-down")["stressed_pnl"][0] == pytest.approx(26.0)


def test_scenario_shocks_are_immutable_and_finite() -> None:
    shocks = {"*": -0.1}
    scenario = StressScenario("down", shocks)
    shocks["*"] = -0.5

    assert scenario.shocks["*"] == -0.1
    with pytest.raises(ValueError, match="finite"):
        StressScenario("bad", {"*": np.nan})
