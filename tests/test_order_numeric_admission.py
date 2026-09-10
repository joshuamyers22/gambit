from types import SimpleNamespace

import numpy as np
import pytest

from gambit.boundaries import BacktestCallbackError
from gambit.callback_contracts import validate_rule_orders
from gambit.pq_types import Contract, ContractGroup, LimitOrder, MarketOrder, OrderStatus, TimeInForce, VWAPOrder
from gambit.risk import MaxOrderQuantity, PolicyResult
from gambit.strategy import Strategy


def setup_order(kind=MarketOrder, sign=1):
    group = ContractGroup.get("numeric-admission")
    contract = Contract.create("ADMISSION", group)
    timestamp = np.datetime64("2026-09-10T10:00", "ns")
    terms = {"limit_price": 100.0} if kind is LimitOrder else {}
    if kind is VWAPOrder:
        terms = {"vwap_end_time": timestamp + np.timedelta64(1, "m")}
    order = kind(contract=contract, timestamp=timestamp, qty=sign * 2, **terms)
    return group, timestamp, order


@pytest.mark.parametrize("kind", [MarketOrder, LimitOrder, VWAPOrder])
@pytest.mark.parametrize("quantity", [0, 0.5, -0.5, np.nan, np.inf, -np.inf, True, np.bool_(False), "2", [2]])
def test_rule_rechecks_mutated_order_quantity(kind, quantity):
    group, timestamp, order = setup_order(kind)
    order.qty = quantity
    with pytest.raises(ValueError, match="order qty must be finite and nonzero, in whole"):
        validate_rule_orders([order], group, timestamp)
    assert order.qty is quantity and order.status is OrderStatus.OPEN


@pytest.mark.parametrize("price", [np.nan, np.inf, -np.inf, True, np.bool_(True), "100", [100]])
def test_rule_rechecks_mutated_limit_price(price):
    group, timestamp, order = setup_order(LimitOrder)
    order.limit_price = price
    with pytest.raises(ValueError, match="limit price must be a finite real number"):
        validate_rule_orders([order], group, timestamp)
    assert order.limit_price is price and order.qty == 2


@pytest.mark.parametrize("kind", [MarketOrder, LimitOrder, VWAPOrder])
@pytest.mark.parametrize("quantity", [3, -3, 3.0, -3.0, np.int64(4), np.float64(-4)])
def test_valid_quantity_edits_preserve_order_identity_and_metadata(kind, quantity):
    group, timestamp, order = setup_order(kind)
    order.qty = quantity
    order.properties.note = "edited before submission"
    result = validate_rule_orders((order,), group, timestamp)
    assert len(result) == 1 and result[0] is order
    assert order.qty is quantity and order.properties.note == "edited before submission"


@pytest.mark.parametrize("price", [-10, 0, 100.0, np.float64(-0.5)])
def test_finite_negative_zero_and_numpy_limits_remain_supported(price):
    group, timestamp, order = setup_order(LimitOrder)
    order.limit_price = price
    assert validate_rule_orders([order], group, timestamp)[0] is order
    assert order.limit_price is price


@pytest.mark.parametrize("bad_field,bad_value", [("qty", np.nan), ("qty", 0.5), ("limit_price", np.inf)])
@pytest.mark.parametrize("invalid_first", [False, True])
def test_invalid_batch_never_reaches_risk_or_execution(bad_field, bad_value, invalid_first):
    group, timestamp, invalid = setup_order(LimitOrder)
    strategy = Strategy(np.array([timestamp]), [group], lambda *_: 100.0, trade_lag=0,
                        log_orders=False, log_trades=False)
    pending = MarketOrder(contract=invalid.contract, timestamp=timestamp, qty=1, time_in_force=TimeInForce.GTC)
    valid = MarketOrder(contract=invalid.contract, timestamp=timestamp, qty=2)
    strategy._current_orders = [pending]
    risk_calls = []
    simulator_calls = []

    class ProbePolicy:
        name = "probe"

        def evaluate(self, order, _context):
            risk_calls.append(order)
            return PolicyResult(True)

    def simulator(orders, *_args):
        simulator_calls.append(tuple(orders))
        return []

    def rule(*_args):
        pending.request_cancel()
        setattr(invalid, bad_field, bad_value)
        return [invalid, valid] if invalid_first else [valid, invalid]

    strategy.add_risk_policy(MaxOrderQuantity(10))
    strategy.add_risk_policy(ProbePolicy())
    strategy.add_market_sim(simulator)
    strategy.position_filters["test"] = None
    strategy.orders_iter = [[(rule, group, {"indicator_values": SimpleNamespace(),
                                          "signal_values": np.array([True]), "rule_name": "test"})]]
    with pytest.raises(BacktestCallbackError) as error:
        strategy._run_iteration(0)
    assert ("order qty" if bad_field == "qty" else "limit price") in str(error.value.__cause__)
    assert risk_calls == [] and strategy.order_decisions == []
    # The initial simulation pass sees only the already-pending order.
    assert len(simulator_calls) == 1 and simulator_calls[0] == (pending,)
    assert strategy._orders == [] and strategy._current_orders == [pending]
    assert pending.status is OrderStatus.OPEN and pending.qty == 1
    assert strategy.account.trade_count == 0 and strategy.account.symbols() == []
