from types import SimpleNamespace

import numpy as np
import pytest

from gambit.boundaries import BacktestCallbackError
from gambit.callback_contracts import validate_rule_orders
from gambit.pq_types import (
    Contract,
    ContractGroup,
    LimitOrder,
    MarketOrder,
    OrderStatus,
    RollOrder,
    TimeInForce,
    VWAPOrder,
)
from gambit.risk import DecisionStatus, MaxPositionQuantity
from gambit.strategy import Strategy


def setup_orders(kind=MarketOrder):
    group = ContractGroup.get("submission-identity")
    contract = Contract.create("IDENTITY", group)
    timestamp = np.datetime64("2026-09-10T10:00", "ns")
    terms = {}
    if kind is LimitOrder:
        terms = {"limit_price": 100.0}
    elif kind is VWAPOrder:
        terms = {"vwap_end_time": timestamp + np.timedelta64(1, "m")}
    elif kind is RollOrder:
        terms = {"reopen_contract": Contract.create("IDENTITY-NEXT", group), "close_qty": -2, "reopen_qty": 2}
    if kind is not RollOrder:
        terms["qty"] = 2
    first = kind(contract=contract, timestamp=timestamp, time_in_force=TimeInForce.GTC, **terms)
    second = kind(contract=contract, timestamp=timestamp, time_in_force=TimeInForce.GTC, **terms)
    return group, timestamp, first, second


@pytest.mark.parametrize("kind", [MarketOrder, LimitOrder, VWAPOrder, RollOrder])
@pytest.mark.parametrize("separated", [False, True])
@pytest.mark.parametrize("container", [list, tuple])
def test_rule_rejects_repeated_object_identity(kind, separated, container):
    group, timestamp, first, second = setup_orders(kind)
    batch = [first, second, first] if separated else [first, first]
    with pytest.raises(ValueError, match="duplicate or already-pending"):
        validate_rule_orders(container(batch), group, timestamp)
    assert first.status is second.status is OrderStatus.OPEN


@pytest.mark.parametrize("kind", [MarketOrder, LimitOrder, VWAPOrder, RollOrder])
def test_distinct_equal_value_orders_remain_distinct(kind):
    group, timestamp, first, second = setup_orders(kind)
    assert first == second and first is not second
    orders = validate_rule_orders([first, second], group, timestamp)
    if kind is RollOrder:
        assert [order.qty for order in orders] == [-2, 2, -2, 2]
        assert len({id(order) for order in orders}) == 4
        assert orders[0].properties._gambit_roll_id != orders[2].properties._gambit_roll_id
    else:
        assert orders[0] is first and orders[1] is second


def schedule(strategy, group, callback):
    strategy.position_filters["test"] = None
    strategy.orders_iter = [[(callback, group, {"indicator_values": SimpleNamespace(),
                                               "signal_values": np.array([True]), "rule_name": "test"})]]


@pytest.mark.parametrize("lag", [0, 1])
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_alias_rejects_whole_batch_without_new_decisions(lag, pending, cancel):
    group, timestamp, first, second = setup_orders()
    strategy = Strategy(np.array([timestamp]), [group], lambda *_: 100.0, trade_lag=lag, log_trades=False)
    # Preserve an unrelated pending order and check cancellation rollback too.
    unrelated = MarketOrder(contract=first.contract, timestamp=timestamp, qty=1, time_in_force=TimeInForce.GTC)
    strategy._current_orders = [unrelated, first] if pending else [unrelated]
    before = list(strategy._current_orders)
    strategy.add_risk_policy(MaxPositionQuantity(3))

    def rule(*_args):
        if cancel:
            unrelated.request_cancel()
            if pending:
                first.cancel()
        return [second, first] if pending else [second, first, first]

    schedule(strategy, group, rule)
    with pytest.raises(BacktestCallbackError) as error:
        strategy._run_iteration(0)
    assert "duplicate or already-pending" in str(error.value.__cause__)
    assert strategy.order_decisions == [] and strategy._orders == []
    assert len(strategy._current_orders) == len(before)
    assert all(actual is original for actual, original in zip(strategy._current_orders, before, strict=True))
    assert all(order.status is OrderStatus.OPEN for order in before)
    assert first.qty == second.qty == 2 and unrelated.qty == 1
    assert strategy.account.trade_count == 0


def test_resubmission_in_later_rule_preserves_prior_decision():
    group, timestamp, first, _ = setup_orders()
    strategy = Strategy(np.array([timestamp]), [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    schedule(strategy, group, lambda *_: [first])
    strategy._run_iteration(0)
    decision = strategy.order_decisions[0]
    assert decision.status is DecisionStatus.ACCEPTED
    with pytest.raises(BacktestCallbackError, match="rule callback failed"):
        strategy._run_iteration(0)
    assert len(strategy.order_decisions) == 1 and strategy.order_decisions[0] is decision
    assert strategy._orders == [first] and strategy._current_orders == [first]
    assert first.status is OrderStatus.OPEN and first.qty == 2


def test_distinct_equal_pending_order_does_not_block_new_order():
    group, timestamp, first, second = setup_orders()
    result = validate_rule_orders([second], group, timestamp, pending_orders=(first,))
    assert result[0] is second


@pytest.mark.parametrize("status", [OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_REQUESTED])
def test_pending_identity_check_does_not_depend_on_lifecycle_status(status):
    group, timestamp, first, _ = setup_orders()
    first.status = status
    with pytest.raises(ValueError, match="already-pending"):
        validate_rule_orders([first], group, timestamp, pending_orders=(first,))
    assert first.status is status and first.qty == 2


def test_distinct_equal_orders_receive_independent_risk_decisions():
    group, timestamp, first, second = setup_orders()
    strategy = Strategy(np.array([timestamp]), [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    strategy.add_risk_policy(MaxPositionQuantity(3))
    schedule(strategy, group, lambda *_: [first, second])
    strategy._run_iteration(0)
    assert [decision.status for decision in strategy.order_decisions] == [DecisionStatus.ACCEPTED, DecisionStatus.REJECTED]
    assert first.status is OrderStatus.OPEN and second.status is OrderStatus.CANCELLED
    assert len(strategy._current_orders) == 1 and strategy._current_orders[0] is first
