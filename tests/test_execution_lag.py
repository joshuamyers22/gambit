from __future__ import annotations

import numpy as np
import pytest

from gambit.pq_types import Contract, ContractGroup, MarketOrder, OrderStatus, RollOrder, TimeInForce, Trade
from gambit.strategy import BacktestCallbackError, Strategy
from gambit.strategy_components import SimpleMarketSimulator


def price(*_args):
    return 100.0


def setup_strategy(lag=3, timestamps=None):
    if timestamps is None:
        timestamps = np.datetime64("2024-01-02T09:30", "ns") + np.arange(7) * np.timedelta64(1, "m")
    group = ContractGroup.get("execution-lag")
    contract = Contract.create("LAG", group)
    strategy = Strategy(timestamps, [group], price, trade_lag=lag, log_trades=False)
    return strategy, contract


def order_at(strategy, contract, index, *, tif=TimeInForce.GTC, qty=2):
    return MarketOrder(contract=contract, timestamp=strategy.timestamps[index], qty=qty, time_in_force=tif)


@pytest.mark.parametrize("lag", [0, 1, 3, 10])
@pytest.mark.parametrize("qty", [2, -2])
def test_rule_order_waits_for_exact_heartbeat_lag(lag, qty):
    strategy, contract = setup_strategy(lag)
    strategy.add_signal("entry", lambda *_args: np.arange(7) == 1)
    strategy.add_rule("entry", lambda *_args: [order_at(strategy, contract, 1, qty=qty)], "entry")
    strategy.add_market_sim(SimpleMarketSimulator(price))

    strategy.run()

    trades = strategy.trades()
    if 1 + lag < len(strategy.timestamps):
        assert [(trade.timestamp, trade.qty) for trade in trades] == [(strategy.timestamps[1 + lag], qty)]
    else:
        assert trades == []
        assert len(strategy._current_orders) == 1
        assert strategy._current_orders[0].status is OrderStatus.OPEN


def test_each_simulator_sees_only_eligible_orders_in_submission_order():
    strategy, contract = setup_strategy()
    old = order_at(strategy, contract, 0)
    young = order_at(strategy, contract, 2)
    another_contract = Contract.create("LAG-SECOND", contract.contract_group)
    another_old = order_at(strategy, another_contract, 0)
    strategy._current_orders = [old, young, another_old]
    seen = []

    def partial(orders, index, timestamps, *_args):
        seen.append(tuple(orders))
        return [Trade(contract=contract, order=old, timestamp=timestamps[index], qty=1, price=100)]

    def finish(orders, index, timestamps, *_args):
        seen.append(tuple(orders))
        return [Trade(contract=order.contract, order=order, timestamp=timestamps[index], qty=order.qty, price=100)
                for order in orders]

    strategy.add_market_sim(partial)
    strategy.add_market_sim(finish)
    strategy._sim_market(3)

    assert seen == [(old, another_old), (old, another_old)]
    assert [trade.qty for trade in strategy.trades()] == [1, 1, 2]
    assert strategy._current_orders == [young]
    assert young.status is OrderStatus.OPEN and young.qty == 2


def test_filled_order_is_not_resubmitted_to_next_simulator():
    strategy, contract = setup_strategy()
    old = order_at(strategy, contract, 0)
    young = order_at(strategy, contract, 2)
    strategy._current_orders = [old, young]
    seen = []
    strategy.add_market_sim(SimpleMarketSimulator(price))

    def observer(orders, *_args):
        seen.append(tuple(orders))
        return []

    strategy.add_market_sim(observer)
    strategy._sim_market(3)
    assert seen == [()]
    assert strategy._current_orders == [young]


def test_pending_cancel_is_acknowledged_before_eligibility_without_simulator():
    strategy, contract = setup_strategy()
    order = order_at(strategy, contract, 0)
    order.request_cancel()
    strategy._current_orders = [order]
    strategy._sim_market(1)
    assert order.status is OrderStatus.CANCELLED
    assert strategy._current_orders == []


def test_day_expires_before_lag_while_gtc_waits_across_midnight():
    timestamps = np.array(["2024-01-02T23:59", "2024-01-03T00:00",
                           "2024-01-03T10:00", "2024-01-04T10:00"], dtype="datetime64[ns]")
    strategy, contract = setup_strategy(timestamps=timestamps)
    day = order_at(strategy, contract, 0, tif=TimeInForce.DAY)
    gtc = order_at(strategy, contract, 0)
    strategy._current_orders = [day, gtc]
    strategy.add_market_sim(SimpleMarketSimulator(price))
    strategy._sim_market(1)
    assert day.status is OrderStatus.CANCELLED
    assert strategy._current_orders == [gtc]
    assert strategy.trades() == []
    strategy._sim_market(3)
    assert [(trade.order, trade.timestamp) for trade in strategy.trades()] == [(gtc, timestamps[3])]


def test_fok_window_starts_at_lag_and_expires_after_it():
    strategy, contract = setup_strategy()
    order = order_at(strategy, contract, 0, tif=TimeInForce.FOK)
    strategy._current_orders = [order]
    seen = []

    def no_fill(orders, index, *_args):
        seen.append((index, tuple(orders)))
        return []

    strategy.add_market_sim(no_fill)
    for index in range(5):
        strategy._sim_market(index)
        if index <= 3:
            assert order.status is OrderStatus.OPEN
    assert seen == [(0, ()), (1, ()), (2, ()), (3, (order,)), (4, ())]
    assert order.status is OrderStatus.CANCELLED


def test_simulator_cannot_report_a_fill_for_an_ineligible_order():
    strategy, contract = setup_strategy()
    order = order_at(strategy, contract, 0)
    strategy._current_orders = [order]

    def early_fill(_orders, index, timestamps, *_args):
        return [Trade(contract=contract, order=order, timestamp=timestamps[index], qty=2, price=100)]

    strategy.add_market_sim(early_fill)
    with pytest.raises(BacktestCallbackError) as error:
        strategy._sim_market(1)
    assert "outside the open order set" in str(error.value.__cause__)
    assert order.qty == 2 and order.status is OrderStatus.OPEN
    assert strategy.trades() == []


def test_failed_eligible_fill_rolls_back_without_dropping_waiting_orders():
    strategy, contract = setup_strategy()
    old = order_at(strategy, contract, 0)
    young = order_at(strategy, contract, 2)
    strategy._current_orders = [old, young]

    def broken(orders, *_args):
        assert tuple(orders) == (old,)
        old.fill(1)
        raise ValueError("simulator failure")

    strategy.add_market_sim(broken)
    with pytest.raises(BacktestCallbackError):
        strategy._sim_market(3)
    assert old.qty == young.qty == 2
    assert old.status is young.status is OrderStatus.OPEN
    assert strategy._current_orders == [old, young]
    assert strategy.trades() == []


def test_partial_gtc_fill_remains_eligible_on_later_heartbeat():
    strategy, contract = setup_strategy()
    order = order_at(strategy, contract, 0)
    strategy._current_orders = [order]

    def partial(orders, index, timestamps, *_args):
        return [Trade(contract=item.contract, order=item, timestamp=timestamps[index], qty=1, price=100)
                for item in orders]

    strategy.add_market_sim(partial)
    for index in range(4):
        strategy._sim_market(index)
    assert order.qty == 1 and order.status is OrderStatus.PARTIALLY_FILLED
    strategy._sim_market(4)
    assert [trade.timestamp for trade in strategy.trades()] == list(strategy.timestamps[3:5])
    assert order.status is OrderStatus.FILLED
    assert strategy._current_orders == []


def test_roll_legs_wait_for_same_execution_heartbeat():
    strategy, outgoing = setup_strategy()
    incoming = Contract.create("LAG-ROLL-IN", outgoing.contract_group)
    strategy.add_signal("roll", lambda *_args: np.arange(7) == 1)

    def roll(*_args):
        return [RollOrder(contract=outgoing, reopen_contract=incoming,
                          timestamp=strategy.timestamps[1], close_qty=-2, reopen_qty=2)]

    strategy.add_rule("roll", roll, "roll")
    strategy.add_market_sim(SimpleMarketSimulator(price))
    strategy.run()
    assert [(trade.contract, trade.timestamp, trade.qty) for trade in strategy.trades()] == [
        (outgoing, strategy.timestamps[4], -2), (incoming, strategy.timestamps[4], 2),
    ]
