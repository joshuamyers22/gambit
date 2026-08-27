import warnings
from types import SimpleNamespace

import numpy as np
import pytest

from gambit.account import Account
from gambit.calculation import CalculationContext, CalculationMode, MissingDataPolicy
from gambit.instruments import AssetClass, InstrumentSpec
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Trade
from gambit.risk_reporting import StressScenario, analyze_account_risk

TIMESTAMP = np.datetime64("2024-01-02T16:00")


def _price(contract, _timestamps, _index, context):
    return context.prices[contract.symbol]


def _account(stock_price: float = 100.0):
    group = ContractGroup.get("calculation")
    stock = Contract.create(
        "CALC-STOCK", group, instrument_spec=InstrumentSpec(asset_class=AssetClass.EQUITY)
    )
    future = Contract.create(
        "CALC-FUTURE", group, multiplier=10, instrument_spec=InstrumentSpec(asset_class=AssetClass.FUTURE)
    )
    context = SimpleNamespace(prices={stock.symbol: stock_price, future.symbol: 50.0})
    account = Account([group], np.array([TIMESTAMP]), _price, context)
    account.add_trades(
        [
            Trade(stock, MarketOrder(contract=stock, timestamp=TIMESTAMP, qty=2), TIMESTAMP, 2, 100.0),
            Trade(future, MarketOrder(contract=future, timestamp=TIMESTAMP, qty=-3), TIMESTAMP, -3, 50.0),
        ]
    )
    return account


def test_context_normalizes_values_and_produces_snapshot() -> None:
    scenario = StressScenario("down", {"*": -0.1})
    context = CalculationContext(
        TIMESTAMP,
        market_data_as_of=np.datetime64("2024-01-01"),
        calendar="NYSE",
        base_currency="usd",
        scenarios=(scenario,),
        provenance_reference="run-123",
    )

    assert context.base_currency == "USD"
    assert context.valuation_time.dtype == np.dtype("datetime64[ns]")
    assert context.snapshot()["scenarios"] == ["down"]


def test_context_prevents_lookahead_by_default() -> None:
    with pytest.raises(ValueError, match="look-ahead"):
        CalculationContext(TIMESTAMP, market_data_as_of=TIMESTAMP + np.timedelta64(1, "D"))

    context = CalculationContext(
        TIMESTAMP,
        market_data_as_of=TIMESTAMP + np.timedelta64(1, "D"),
        allow_lookahead=True,
    )
    assert context.market_data_as_of > context.valuation_time


def test_historical_context_requires_a_range() -> None:
    with pytest.raises(ValueError, match="require"):
        CalculationContext(TIMESTAMP, mode=CalculationMode.HISTORICAL)


def test_context_scenarios_flow_into_account_report() -> None:
    context = CalculationContext(TIMESTAMP, scenarios=(StressScenario("down", {"*": -0.1}),))

    report = analyze_account_risk(_account(), context)

    assert report.scenario_results["scenario"].to_list() == ["down"]


def test_missing_data_policy_can_warn_and_skip() -> None:
    account = _account(stock_price=np.nan)
    context = CalculationContext(TIMESTAMP, missing_data_policy=MissingDataPolicy.WARN)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        report = analyze_account_risk(account, context)

    assert len(caught) == 1
    assert report.exposures["symbol"].to_list() == ["CALC-FUTURE"]
