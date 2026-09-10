import numpy as np
import pytest

from gambit.boundaries import BacktestCallbackError
from gambit.callback_contracts import validate_market_trades
from gambit.pq_types import Contract, ContractGroup, MarketOrder, OrderStatus, Trade
from gambit.strategy import Strategy


def price(*_args):
    return 100.0


def setup_order(sign=1):
    group = ContractGroup.get("fill-quantities")
    contract = Contract.create("FILL-QTY", group)
    timestamps = np.array(["2026-01-02T09:30", "2026-01-02T09:31"], dtype="datetime64[ns]")
    strategy = Strategy(timestamps, [group], price, trade_lag=0, log_trades=False)
    order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=sign * 2)
    strategy._current_orders = [order]
    return strategy, order


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("fills", [[3], [1, 2], [-1], [3, -1], [1, -1]])
@pytest.mark.parametrize("apply_state", [False, True])
def test_invalid_fill_quantities_never_reach_account(sign, fills, apply_state):
    strategy, order = setup_order(sign)
    quantities = [sign * qty for qty in fills]

    def simulator(*_args):
        trades = [Trade(order.contract, order, strategy.timestamps[0], qty, 100) for qty in quantities]
        if apply_state and sum(quantities):
            # A custom simulator can assign state directly, bypassing Order.fill.
            order.qty -= sum(quantities)
            order.status = OrderStatus.FILLED if order.qty == 0 else OrderStatus.PARTIALLY_FILLED
        return trades

    strategy.add_market_sim(simulator)
    with pytest.raises(BacktestCallbackError):
        strategy._sim_market(0)
    assert order.qty == sign * 2 and order.status is OrderStatus.OPEN
    assert strategy._current_orders == [order]
    assert strategy.account.trade_count == 0
    assert strategy.account.symbols() == []


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("apply_state", [False, True])
@pytest.mark.parametrize("fills", [[1], [1, 1], [2]])
def test_valid_partial_and_split_fills_preserve_callback_order(sign, apply_state, fills):
    strategy, order = setup_order(sign)

    def simulator(*_args):
        trades = [Trade(order.contract, order, strategy.timestamps[0], sign * qty, 100 + index)
                  for index, qty in enumerate(fills)]
        if apply_state:
            order.fill(sign * sum(fills))
        return trades

    strategy.add_market_sim(simulator)
    strategy._sim_market(0)
    assert [(trade.qty, trade.price) for trade in strategy.trades()] == [
        (sign * qty, 100 + index) for index, qty in enumerate(fills)
    ]
    assert order.qty == sign * (2 - sum(fills))
    expected_status = OrderStatus.FILLED if order.qty == 0 else OrderStatus.PARTIALLY_FILLED
    assert order.status is expected_status


@pytest.mark.parametrize("sign", [1, -1])
def test_later_simulator_cannot_overfill_remaining_quantity(sign):
    strategy, order = setup_order(sign)

    def partial(*_args):
        return [Trade(order.contract, order, strategy.timestamps[0], sign, 100)]

    def excess(*_args):
        order.qty -= sign * 2
        order.status = OrderStatus.PARTIALLY_FILLED
        return [Trade(order.contract, order, strategy.timestamps[0], sign * 2, 101)]

    strategy.add_market_sim(partial)
    strategy.add_market_sim(excess)
    with pytest.raises(BacktestCallbackError):
        strategy._sim_market(0)
    # Rollback is scoped to the failed callback; an earlier valid fill remains.
    assert order.qty == sign and order.status is OrderStatus.PARTIALLY_FILLED
    assert [trade.qty for trade in strategy.trades()] == [sign]


def test_one_invalid_order_rejects_whole_callback_batch():
    strategy, first = setup_order()
    second = MarketOrder(contract=first.contract, timestamp=first.timestamp, qty=2)
    strategy._current_orders.append(second)

    def simulator(*_args):
        first.fill(1)
        second.qty = -1
        second.status = OrderStatus.PARTIALLY_FILLED
        return [Trade(first.contract, first, first.timestamp, 1, 100),
                Trade(second.contract, second, second.timestamp, 3, 100)]

    strategy.add_market_sim(simulator)
    with pytest.raises(BacktestCallbackError):
        strategy._sim_market(0)
    assert first.qty == second.qty == 2
    assert first.status is second.status is OrderStatus.OPEN
    assert strategy.trades() == []


@pytest.mark.parametrize("quantity", [0, 0.5, np.nan, np.inf, True, "1"])
def test_mutated_trade_quantity_is_revalidated_at_callback_boundary(quantity):
    strategy, order = setup_order()
    trade = Trade(order.contract, order, strategy.timestamps[0], 1, 100)
    trade.qty = quantity
    with pytest.raises(ValueError, match="trade qty must be finite and nonzero, in whole"):
        validate_market_trades([trade], [order], strategy.timestamps[0], {id(order): (2, OrderStatus.OPEN)})
    assert order.qty == 2 and order.status is OrderStatus.OPEN


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("filled", [0, 1, 2])
def test_valid_fill_then_cancellation_remains_supported(sign, filled):
    strategy, order = setup_order(sign)

    def simulator(*_args):
        trades = []
        if filled:
            trades = [Trade(order.contract, order, strategy.timestamps[0], sign * filled, 100)]
            order.fill(sign * filled)
        order.cancel()
        return trades

    strategy.add_market_sim(simulator)
    strategy._sim_market(0)
    assert sum(trade.qty for trade in strategy.trades()) == sign * filled
    assert order.qty == sign * (2 - filled)
    assert order.status is (OrderStatus.FILLED if filled == 2 else OrderStatus.CANCELLED)
