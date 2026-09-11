from types import SimpleNamespace

import numpy as np
import pytest

from gambit.boundaries import BacktestCallbackError
from gambit.callback_contracts import validate_rule_orders
from gambit.pq_types import Contract, MarketOrder, OrderStatus, VWAPOrder
from gambit.risk import PolicyResult
from gambit.strategy import Strategy
from gambit.strategy_components import VWAPMarketSimulator

TIMESTAMPS = np.array(["2026-09-10T10:00", "2026-09-10T10:01"], dtype="datetime64[ns]")
INVALID_ENDS = [
    (np.datetime64("NaT"), ValueError, "cannot be NaT"),
    (TIMESTAMPS[0] - np.timedelta64(1, "ns"), ValueError, "cannot precede"),
    ("2026-09-10T10:01", TypeError, "numpy datetime64"),
    (np.array(TIMESTAMPS[1]), TypeError, "numpy datetime64"),
    (None, TypeError, "numpy datetime64"),
    (True, TypeError, "numpy datetime64"),
    (1, TypeError, "numpy datetime64"),
]


def make_order(qty=2, end=None):
    return VWAPOrder(contract=Contract.get_or_create("VWAP-WINDOW"), timestamp=TIMESTAMPS[0], qty=qty,
                     vwap_end_time=TIMESTAMPS[1] if end is None else end)


def indicators(order):
    return {order.contract.contract_group.name: SimpleNamespace(
        price=np.array([100., 102.]), volume=np.array([1., 3.]), backup=np.array([99., 101.]))}


@pytest.mark.parametrize("qty", [2, -2])
@pytest.mark.parametrize("end,error,message", INVALID_ENDS)
@pytest.mark.parametrize("boundary", ["construction", "admission", "execution"])
def test_invalid_window_uses_same_policy_at_each_boundary(qty, end, error, message, boundary):
    order = make_order(qty)
    with pytest.raises(error, match=message):
        if boundary == "construction":
            VWAPOrder(contract=order.contract, timestamp=TIMESTAMPS[0], qty=qty, vwap_end_time=end)
        else:
            order.vwap_end_time = end
            if boundary == "admission":
                validate_rule_orders([order], order.contract.contract_group, TIMESTAMPS[0])
            else:
                VWAPMarketSimulator("price", "volume", "backup")(
                    [order], 0, TIMESTAMPS, indicators(order), {}, SimpleNamespace())
    assert order.qty == qty and order.status is OrderStatus.OPEN


@pytest.mark.parametrize("invalid_first", [False, True])
@pytest.mark.parametrize("stop", [np.nan, 105.])
def test_direct_batch_checks_windows_before_any_fill_or_cancellation(invalid_first, stop):
    valid = make_order(end=TIMESTAMPS[0])
    invalid = make_order()
    invalid.vwap_end_time = np.datetime64("NaT")
    valid.vwap_stop = stop
    orders = [invalid, valid] if invalid_first else [valid, invalid]
    with pytest.raises(ValueError, match="cannot be NaT"):
        # Empty indicators prove rejection precedes pricing/backup lookup, too.
        VWAPMarketSimulator("price", "volume", "backup")(
            orders, 0, TIMESTAMPS, {}, {}, SimpleNamespace())
    assert all(order.qty == 2 and order.status is OrderStatus.OPEN for order in orders)


@pytest.mark.parametrize("stage", ["rule", "risk"])
@pytest.mark.parametrize("invalid_first", [False, True])
@pytest.mark.parametrize("end", [np.datetime64("NaT"), TIMESTAMPS[0] - np.timedelta64(1, "ns")])
def test_invalid_strategy_window_never_reaches_accounting(stage, invalid_first, end):
    valid = make_order(end=TIMESTAMPS[0])
    invalid = make_order()
    orders = [invalid, valid] if invalid_first else [valid, invalid]
    group = valid.contract.contract_group
    strategy = Strategy(TIMESTAMPS, [group], lambda *_: 100., trade_lag=0,
                        log_orders=False, log_trades=False)
    risk_calls = []

    class Policy:
        name = "window-probe"

        def evaluate(self, order, _context):
            risk_calls.append(order)
            if stage == "risk" and order is invalid:
                order.vwap_end_time = end
            return PolicyResult(True)

    def rule(*_args):
        if stage == "rule":
            invalid.vwap_end_time = end
        return orders

    strategy.add_risk_policy(Policy())
    strategy.add_market_sim(VWAPMarketSimulator("price", "volume", "backup"))
    strategy.indicator_values = indicators(valid)
    strategy.position_filters["test"] = None
    strategy.orders_iter = [[(rule, group, {"indicator_values": SimpleNamespace(),
                                          "signal_values": np.array([True, False]), "rule_name": "test"})], []]
    with pytest.raises(BacktestCallbackError) as error:
        strategy._run_iteration(0)
    assert "VWAP" in str(error.value.__cause__)
    assert risk_calls == ([] if stage == "rule" else orders)
    assert len(strategy.order_decisions) == (0 if stage == "rule" else 2)
    assert all(order.qty == 2 and order.status is OrderStatus.OPEN for order in orders)
    assert strategy.account.trade_count == 0 and strategy.account.symbols() == []
    # Window terms remain outside the protected-field rollback contract.
    assert invalid.vwap_end_time is end


@pytest.mark.parametrize("qty", [2, -2])
@pytest.mark.parametrize("end,index,price", [
    (TIMESTAMPS[0], 0, 100.),
    (TIMESTAMPS[1].astype("datetime64[s]"), 1, 101.5),
    (TIMESTAMPS[1] + np.timedelta64(1, "h"), 1, 101.5),
])
def test_valid_window_edits_preserve_zero_duration_units_and_final_execution(qty, end, index, price):
    order = make_order(qty)
    order.vwap_end_time = end
    assert validate_rule_orders([order], order.contract.contract_group, TIMESTAMPS[0])[0] is order
    trades = VWAPMarketSimulator("price", "volume")(
        [order], index, TIMESTAMPS, indicators(order), {}, SimpleNamespace())
    assert len(trades) == 1 and trades[0].price == price and trades[0].qty == qty
    assert trades[0].timestamp == TIMESTAMPS[index] and order.status is OrderStatus.FILLED


def test_non_vwap_orders_remain_ignored():
    vwap = make_order()
    ordinary = MarketOrder(contract=vwap.contract, timestamp=TIMESTAMPS[0], qty=2)
    trades = VWAPMarketSimulator("price", "volume")(
        [ordinary], 0, TIMESTAMPS, {}, {}, SimpleNamespace())
    assert trades == [] and ordinary.status is OrderStatus.OPEN
