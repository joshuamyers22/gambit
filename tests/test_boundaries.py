from types import SimpleNamespace

import numpy as np
import pytest

from gambit.account import Account
from gambit.boundaries import BacktestCallbackError
from gambit.callback_contracts import validate_market_trades, validate_rule_orders
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Trade
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator


def _price(_contract, _timestamps, _index, _context):
    return 100.0


@pytest.mark.parametrize(
    ("timestamps", "error", "message"),
    [
        (np.array([], dtype="datetime64[ns]"), ValueError, "cannot be empty"),
        (np.array(["NaT"], dtype="datetime64[ns]"), ValueError, "cannot contain NaT"),
        (
            np.array(["2026-01-01", "2026-01-01"], dtype="datetime64[D]"),
            ValueError,
            "strictly increasing",
        ),
        (np.array([1, 2]), TypeError, "datetime64 dtype"),
        (np.array([["2026-01-01"]], dtype="datetime64[D]"), ValueError, "one-dimensional"),
    ],
)
def test_strategy_rejects_invalid_timestamp_grids(timestamps, error, message) -> None:
    group = ContractGroup.get("timestamp-boundary")

    with pytest.raises(error, match=message):
        Strategy(timestamps, [group], _price)


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), "100", [100.0], True])
def test_market_simulator_rejects_invalid_price_callback_values(value) -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("price-boundary")
    contract = Contract.create("PRICE-BOUNDARY", group)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    simulator = SimpleMarketSimulator(lambda *_args: value)

    with pytest.raises((TypeError, ValueError), match="price"):
        simulator([order], 0, np.array([timestamp]), {}, {}, SimpleNamespace())


def test_missing_mark_carries_unrealized_pnl_without_nan_contamination() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    group = ContractGroup.get("missing-mark")
    contract = Contract.create("MISSING-MARK", group)

    def price(_contract, _timestamps, index, _context):
        return 100.0 if index == 0 else float("nan")

    account = Account([group], timestamps, price, SimpleNamespace(), starting_equity=1_000.0)
    order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=1.0)
    account.add_trades([Trade(contract, order, timestamps[0], 1.0, 100.0)])

    account.calc(timestamps[1])

    assert np.isfinite(account.equity(timestamps[1]))
    assert account.equity(timestamps[1]) == 1_000.0


def test_rule_contract_violation_is_chained_with_context() -> None:
    timestamp = np.datetime64("2026-01-01")
    expected_group = ContractGroup.get("expected-rule-group")
    other_group = ContractGroup.get("other-rule-group")
    wrong_contract = Contract.create("WRONG-GROUP", other_group)
    strategy = Strategy(np.array([timestamp]), [expected_group], _price)
    strategy.position_filters["bad-rule"] = None

    def bad_rule(*_args):
        return [MarketOrder(contract=wrong_contract, timestamp=timestamp, qty=1.0)]

    with pytest.raises(BacktestCallbackError, match="expected-rule-group") as raised:
        strategy._get_orders(
            0,
            bad_rule,
            expected_group,
            {"indicator_values": SimpleNamespace(), "signal_values": np.array([True]), "rule_name": "bad-rule"},
        )

    assert isinstance(raised.value.__cause__, ValueError)


def test_invalid_market_simulator_output_is_chained_before_account_mutation() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("sim-boundary")
    contract = Contract.create("SIM-BOUNDARY", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    strategy._current_orders = [order]
    strategy.market_sims = [lambda *_args: [object()]]

    with pytest.raises(BacktestCallbackError, match="market simulator") as raised:
        strategy._sim_market(0)

    assert isinstance(raised.value.__cause__, TypeError)
    assert strategy.trades() == []


def test_callback_contracts_normalize_rule_orders_without_strategy_state() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("pure-rule-contract")
    order = MarketOrder(contract=Contract.create("PURE-RULE", group), timestamp=timestamp, qty=1.0)

    result = validate_rule_orders((order,), group)

    assert result == [order]


def test_callback_contracts_reject_trade_for_unknown_order_without_account_mutation() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("pure-trade-contract")
    contract = Contract.create("PURE-TRADE", group)
    open_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    unknown_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    trade = Trade(contract, unknown_order, timestamp, 1.0, 100.0)

    with pytest.raises(ValueError, match="outside the open order set"):
        validate_market_trades([trade], [open_order], timestamp)
