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
from gambit.risk import MaxOrderQuantity
from gambit.strategy import Strategy


def setup_order(kind=MarketOrder):
    group = ContractGroup.get("reference-admission")
    contract = Contract.create("ADMISSION-REF", group)
    timestamp = np.datetime64("2026-09-10T23:59", "ns")
    terms = {"qty": 2}
    if kind is LimitOrder:
        terms["limit_price"] = 100.0
    elif kind is VWAPOrder:
        terms["vwap_end_time"] = timestamp + np.timedelta64(1, "m")
    elif kind is RollOrder:
        terms = {"reopen_contract": Contract.create("ADMISSION-NEXT", group), "close_qty": -2, "reopen_qty": 2}
    order = kind(contract=contract, timestamp=timestamp, time_in_force=TimeInForce.DAY, **terms)
    return group, timestamp, order


@pytest.mark.parametrize("kind", [MarketOrder, LimitOrder, VWAPOrder, RollOrder])
@pytest.mark.parametrize("field,value,message", [
    ("contract", None, "order contract must be a Contract"),
    ("contract", SimpleNamespace(symbol="ADMISSION-REF"), "order contract must be a Contract"),
    ("timestamp", "2026-09-10T23:59", "order timestamp must be a numpy datetime64"),
    ("timestamp", np.array(np.datetime64("2026-09-10T23:59", "ns")), "order timestamp must be a numpy datetime64"),
    ("timestamp", np.array([np.datetime64("2026-09-10T23:59", "ns")]), "order timestamp must be a numpy datetime64"),
    ("time_in_force", "DAY", "order time_in_force must be a TimeInForce"),
    ("time_in_force", 3, "order time_in_force must be a TimeInForce"),
    ("time_in_force", None, "order time_in_force must be a TimeInForce"),
    ("status", "OPEN", "order status must be an OrderStatus"),
    ("status", 1, "order status must be an OrderStatus"),
    ("status", True, "order status must be an OrderStatus"),
])
def test_mutable_order_reference_fields_are_rechecked(kind, field, value, message):
    group, timestamp, order = setup_order(kind)
    setattr(order, field, value)
    with pytest.raises(TypeError, match=message):
        validate_rule_orders([order], group, timestamp)
    assert getattr(order, field) is value


@pytest.mark.parametrize("kind", [MarketOrder, LimitOrder, VWAPOrder, RollOrder])
@pytest.mark.parametrize("current_is_nat", [False, True])
def test_missing_submission_timestamp_is_always_rejected(kind, current_is_nat):
    group, timestamp, order = setup_order(kind)
    order.timestamp = np.datetime64("NaT", "ns")
    with pytest.raises(ValueError, match="timestamp does not match"):
        validate_rule_orders([order], group, order.timestamp if current_is_nat else timestamp)


@pytest.mark.parametrize("unit", ["m", "s", "ms", "us", "ns"])
@pytest.mark.parametrize("tif", list(TimeInForce))
def test_valid_numpy_units_and_enum_policies_preserve_identity(unit, tif):
    group, timestamp, order = setup_order()
    order.timestamp = timestamp.astype(f"datetime64[{unit}]")
    order.time_in_force = tif
    assert validate_rule_orders([order], group, timestamp)[0] is order
    assert order.timestamp.dtype == np.dtype(f"datetime64[{unit}]")
    assert order.time_in_force is tif


def test_constructor_still_allows_unscheduled_market_order():
    group = ContractGroup.get("unscheduled")
    order = MarketOrder(contract=Contract.create("UNSCHEDULED", group), qty=1)
    assert np.isnat(order.timestamp)


@pytest.mark.parametrize("field,value", [("time_in_force", "DAY"), ("status", "OPEN"), ("contract", None)])
@pytest.mark.parametrize("invalid_first", [False, True])
def test_invalid_references_reject_batch_and_restore_pending_cancellation(field, value, invalid_first):
    group, timestamp, order = setup_order()
    strategy = Strategy(np.array([timestamp]), [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    pending = MarketOrder(contract=order.contract, timestamp=timestamp, qty=1, time_in_force=TimeInForce.GTC)
    valid = MarketOrder(contract=order.contract, timestamp=timestamp, qty=1)
    strategy._current_orders = [pending]
    strategy.add_risk_policy(MaxOrderQuantity(10))

    def rule(*_args):
        pending.request_cancel()
        setattr(order, field, value)
        return [order, valid] if invalid_first else [valid, order]

    strategy.position_filters["test"] = None
    strategy.orders_iter = [[(rule, group, {"indicator_values": SimpleNamespace(),
                                          "signal_values": np.array([True]), "rule_name": "test"})]]
    with pytest.raises(BacktestCallbackError) as error:
        strategy._run_iteration(0)
    assert isinstance(error.value.__cause__, TypeError)
    assert strategy.order_decisions == [] and strategy._orders == []
    assert strategy._current_orders == [pending] and pending.status is OrderStatus.OPEN
    assert strategy.account.trade_count == 0


@pytest.mark.parametrize("tif", [TimeInForce.DAY, TimeInForce.GTC])
def test_valid_submitted_day_and_gtc_lifetimes_remain_distinct(tif):
    group, timestamp, order = setup_order()
    order.time_in_force = tif
    timestamps = np.array([timestamp, timestamp + np.timedelta64(1, "m")])
    strategy = Strategy(timestamps, [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    strategy.add_signal("entry", lambda *_: np.array([True, False]))
    strategy.add_rule("entry", lambda *_: [order], "entry")
    strategy.run()
    assert len(strategy.order_decisions) == 1
    assert order.status is (OrderStatus.CANCELLED if tif is TimeInForce.DAY else OrderStatus.OPEN)
    assert len(strategy._current_orders) == (0 if tif is TimeInForce.DAY else 1)
