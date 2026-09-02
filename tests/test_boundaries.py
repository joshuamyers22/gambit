from types import SimpleNamespace

import numpy as np
import pytest

from gambit.account import Account
from gambit.boundaries import BacktestCallbackError
from gambit.callback_contracts import validate_market_trades, validate_rule_orders
from gambit.pq_types import Contract, ContractGroup, MarketOrder, OrderStatus, Trade
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


@pytest.mark.parametrize(
    ("timestamps", "error", "message"),
    [
        (np.array([], dtype="datetime64[ns]"), ValueError, "account timestamps cannot be empty"),
        (
            np.array(["2026-01-01", "2026-01-01"], dtype="datetime64[D]"),
            ValueError,
            "account timestamps must be strictly increasing",
        ),
        (np.array([1, 2]), TypeError, "account timestamps must have a datetime64 dtype"),
    ],
)
def test_account_rejects_invalid_timestamp_grids(timestamps, error, message) -> None:
    group = ContractGroup.get("account-timestamp-boundary")

    with pytest.raises(error, match=message):
        Account([group], timestamps, _price, SimpleNamespace())


@pytest.mark.parametrize(
    ("contract_groups", "error", "message"),
    [
        ([], ValueError, "requires at least one contract group"),
        ([object()], TypeError, "only ContractGroup objects"),
        ("not-a-sequence-of-groups", TypeError, "must be a sequence"),
    ],
)
def test_account_rejects_invalid_contract_groups(contract_groups, error, message) -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")

    with pytest.raises(error, match=message):
        Account(contract_groups, timestamps, _price, SimpleNamespace())


def test_account_rejects_duplicate_contract_group_names() -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")
    first = ContractGroup("duplicate-account-group")
    second = ContractGroup("duplicate-account-group")

    with pytest.raises(ValueError, match="contract group names must be unique"):
        Account([first, second], timestamps, _price, SimpleNamespace())


def test_account_rejects_non_callable_price_function() -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")

    with pytest.raises(TypeError, match="price_function must be callable"):
        Account([ContractGroup.get("invalid-price-function")], timestamps, 100.0, SimpleNamespace())


def test_account_rejects_off_grid_valuation_without_leaking_index_error() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("off-grid-account")
    contract = Contract.create("OFF-GRID", group)
    account = Account([group], np.array([timestamp]), _price, SimpleNamespace())
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    account.add_trades([Trade(contract, order, timestamp, 1, 100.0)])

    with pytest.raises(ValueError, match="not present in the account timestamp grid"):
        account.calc(timestamp + np.timedelta64(1, "D"))


def test_empty_account_rejects_off_grid_valuation() -> None:
    timestamp = np.datetime64("2026-01-01")
    account = Account(
        [ContractGroup.get("empty-off-grid-account")],
        np.array([timestamp]),
        _price,
        SimpleNamespace(),
    )

    with pytest.raises(ValueError, match="not present in the account timestamp grid"):
        account.equity(timestamp + np.timedelta64(1, "D"))

    assert not account._pnl


def test_account_rejects_invalid_trade_batch_before_mutation() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    group = ContractGroup.get("atomic-trade-batch")
    contract = Contract.create("ATOMIC-TRADE", group)
    account = Account([group], timestamps, _price, SimpleNamespace())
    valid_order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=1)
    off_grid_timestamp = timestamps[-1] + np.timedelta64(1, "D")
    invalid_order = MarketOrder(contract=contract, timestamp=off_grid_timestamp, qty=1)

    with pytest.raises(ValueError, match="not present in the account timestamp grid"):
        account.add_trades(
            [
                Trade(contract, valid_order, timestamps[0], 1, 100.0),
                Trade(contract, invalid_order, off_grid_timestamp, 1, 100.0),
            ]
        )

    assert account.trades() == []
    assert account.symbols() == []


def test_account_rejects_trade_outside_configured_contract_groups() -> None:
    timestamp = np.datetime64("2026-01-01")
    configured_group = ContractGroup.get("configured-account-group")
    outside_group = ContractGroup.get("outside-account-group")
    contract = Contract.create("OUTSIDE-ACCOUNT", outside_group)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    account = Account([configured_group], np.array([timestamp]), _price, SimpleNamespace())

    with pytest.raises(ValueError, match="outside the account's configured contract groups"):
        account.add_trades([Trade(contract, order, timestamp, 1, 100.0)])

    assert account.symbols() == []


def test_account_rejects_retroactive_cross_contract_batch_before_mutation() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02", "2026-01-03"], dtype="datetime64[D]")
    group = ContractGroup.get("chronological-trade-batch")
    first = Contract.create("CHRONO-FIRST", group)
    second = Contract.create("CHRONO-SECOND", group)
    account = Account([group], timestamps, _price, SimpleNamespace())

    def trade(contract: Contract, timestamp: np.datetime64) -> Trade:
        order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
        return Trade(contract, order, timestamp, 1, 100.0)

    account.add_trades([trade(first, timestamps[1]), trade(second, timestamps[1])])

    with pytest.raises(ValueError, match="non-decreasing timestamps"):
        account.add_trades([trade(first, timestamps[2]), trade(second, timestamps[0])])

    assert account.position(group, timestamps[2]) == 2
    assert len(account.trades()) == 2


def test_account_rolls_back_cross_contract_batch_when_price_callback_fails() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("callback-rollback")
    first = Contract.create("ROLLBACK-FIRST", group)
    second = Contract.create("ROLLBACK-SECOND", group)
    reject_second = False

    def price(contract, *_args):
        if reject_second and contract is second:
            raise RuntimeError("price unavailable")
        return 110.0

    def trade(contract: Contract) -> Trade:
        order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
        return Trade(contract, order, timestamp, 1, 100.0)

    account = Account([group], np.array([timestamp]), price, SimpleNamespace(), starting_equity=1_000.0)
    account.add_trades([trade(first), trade(second)])
    assert account.equity(timestamp) == 1_020.0

    reject_second = True
    with pytest.raises(RuntimeError, match="price unavailable"):
        account.add_trades([trade(first), trade(second)])

    assert account.position(group, timestamp) == 2
    assert len(account.trades()) == 2
    assert account.equity(timestamp) == 1_020.0


@pytest.mark.parametrize(
    ("value", "error"),
    [(-1, ValueError), (1440, ValueError), (900.5, TypeError), (True, TypeError)],
)
def test_account_rejects_invalid_daily_calculation_time(value, error) -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")

    with pytest.raises(error, match="pnl_calc_time"):
        Account([ContractGroup.get("invalid-calc-time")], timestamps, _price, SimpleNamespace(), pnl_calc_time=value)


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


def test_rule_callback_cannot_mutate_current_order_membership() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("immutable-current-orders")
    contract = Contract.create("IMMUTABLE-CURRENT-ORDERS", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    existing_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    strategy._current_orders = [existing_order]
    strategy.position_filters["mutating-rule"] = None

    def mutating_rule(_group, _index, _timestamps, _indicators, _signals, _account, current_orders, _context):
        current_orders.append(MarketOrder(contract=contract, timestamp=timestamp, qty=1))
        return []

    with pytest.raises(BacktestCallbackError, match="rule callback") as raised:
        strategy._get_orders(
            0,
            mutating_rule,
            group,
            {
                "indicator_values": SimpleNamespace(),
                "signal_values": np.array([True]),
                "rule_name": "mutating-rule",
            },
        )

    assert isinstance(raised.value.__cause__, AttributeError)
    assert strategy._current_orders == [existing_order]


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


def test_invalid_market_simulator_output_restores_order_lifecycle_state() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("sim-order-rollback")
    contract = Contract.create("SIM-ORDER-ROLLBACK", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=2)
    strategy._current_orders = [order]

    def invalid_simulator(current_orders, *_args):
        current_orders[0].fill(1)
        return [object()]

    strategy.market_sims = [invalid_simulator]

    with pytest.raises(BacktestCallbackError, match="market simulator"):
        strategy._sim_market(0)

    assert order.qty == 2
    assert order.status is OrderStatus.OPEN
    assert strategy._current_orders == [order]


def test_market_simulator_interruption_restores_order_state_without_wrapping() -> None:
    class ExecutionInterrupted(BaseException):
        pass

    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("sim-order-interruption")
    contract = Contract.create("SIM-ORDER-INTERRUPTION", group)
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=2)
    strategy._current_orders = [order]

    def interrupted_simulator(current_orders, *_args):
        current_orders[0].fill(1)
        raise ExecutionInterrupted

    strategy.market_sims = [interrupted_simulator]

    with pytest.raises(ExecutionInterrupted):
        strategy._sim_market(0)

    assert order.qty == 2
    assert order.status is OrderStatus.OPEN


def test_callback_contracts_normalize_rule_orders_without_strategy_state() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("pure-rule-contract")
    order = MarketOrder(contract=Contract.create("PURE-RULE", group), timestamp=timestamp, qty=1.0)

    result = validate_rule_orders((order,), group, timestamp)

    assert result == [order]


def test_callback_contracts_reject_order_for_wrong_strategy_timestamp() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("wrong-rule-timestamp")
    contract = Contract.create("WRONG-RULE-TIMESTAMP", group)
    order = MarketOrder(contract=contract, timestamp=timestamp + np.timedelta64(1, "D"), qty=1)

    with pytest.raises(ValueError, match="does not match the current strategy timestamp"):
        validate_rule_orders([order], group, timestamp)


def test_callback_contracts_reject_trade_for_unknown_order_without_account_mutation() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("pure-trade-contract")
    contract = Contract.create("PURE-TRADE", group)
    open_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    unknown_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1.0)
    trade = Trade(contract, unknown_order, timestamp, 1.0, 100.0)

    with pytest.raises(ValueError, match="outside the open order set"):
        validate_market_trades([trade], [open_order], timestamp)


def test_callback_contracts_normalize_market_trades_to_detached_list() -> None:
    timestamp = np.datetime64("2026-01-01")
    group = ContractGroup.get("normalized-market-trades")
    contract = Contract.create("NORMALIZED-MARKET-TRADES", group)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    trade = Trade(contract, order, timestamp, 1, 100.0)
    callback_result = (trade,)

    result = validate_market_trades(callback_result, [order], timestamp)

    assert result == [trade]
    assert isinstance(result, list)
