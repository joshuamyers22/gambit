from collections.abc import Sequence

import numpy as np
import pytest

from gambit.callback_contracts import validate_market_trades
from gambit.pq_types import Contract, ContractGroup, MarketOrder, OrderStatus, TimeInForce, Trade
from gambit.strategy import Strategy


class CountedOrders(Sequence):
    def __init__(self, orders):
        self.orders = orders
        self.visits = 0

    def __len__(self):
        return len(self.orders)

    def __getitem__(self, index):
        return self.orders[index]

    def __iter__(self):
        for order in self.orders:
            self.visits += 1
            yield order


class CountedTrade(Trade):
    order_reads = 0

    def __getattribute__(self, name):
        if name == "order":
            type(self).order_reads += 1
        return super().__getattribute__(name)


def setup_batch(count, sign=1, trade_type=Trade):
    group = ContractGroup.get("fill-scaling")
    contracts = [Contract.create(f"FILL-SCALE-{i}", group) for i in range(2)]
    timestamp = np.datetime64("2026-09-10T10:00", "ns")
    orders = [MarketOrder(contract=contracts[i % 2], timestamp=timestamp, qty=sign * 3,
                          time_in_force=TimeInForce.GTC) for i in range(count)]
    trades = [trade_type(order.contract, order, timestamp, sign, 100 + index)
              for index in range(2) for order in reversed(orders)]
    return group, timestamp, orders, trades


@pytest.mark.parametrize("count", [4, 16, 64])
def test_validation_visits_open_orders_only_linearly(count):
    _, timestamp, orders, trades = setup_batch(count)
    counted = CountedOrders(orders)
    original = {id(order): (order.qty, order.status) for order in orders}
    result = validate_market_trades(trades, counted, timestamp, original)
    assert all(actual is expected for actual, expected in zip(result, trades, strict=True))
    assert counted.visits <= 2 * count


@pytest.mark.parametrize("count", [4, 16, 64])
def test_strategy_reconciles_fills_without_per_order_trade_rescans(count, monkeypatch):
    group, timestamp, orders, trades = setup_batch(count, trade_type=CountedTrade)
    strategy = Strategy(np.array([timestamp]), [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    strategy._current_orders = orders.copy()
    strategy.add_market_sim(lambda *_: trades)
    recorded = []
    # Count only validation/reconciliation; account ingestion has separate tests.
    monkeypatch.setattr(strategy.account, "add_trades", recorded.extend)
    CountedTrade.order_reads = 0
    strategy._sim_market(0)
    assert CountedTrade.order_reads <= 6 * len(trades)
    assert all(actual is expected for actual, expected in zip(recorded, trades, strict=True))
    assert all(order.qty == 1 and order.status is OrderStatus.PARTIALLY_FILLED for order in orders)


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("applied", [False, True])
def test_interleaved_multi_instrument_fills_keep_accounting_and_remainders(sign, applied):
    group, timestamp, orders, trades = setup_batch(8, sign)
    strategy = Strategy(np.array([timestamp]), [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    strategy._current_orders = orders.copy()

    def simulator(*_args):
        if applied:
            for order in orders:
                order.fill(sign * 2)
        return trades

    strategy.add_market_sim(simulator)
    strategy._sim_market(0)
    recorded = strategy.trades()
    # Account reports return detached snapshots, in stable callback order.
    assert [(trade.contract.symbol, trade.qty, trade.price) for trade in recorded] == [
        (trade.contract.symbol, trade.qty, trade.price) for trade in trades
    ]
    assert strategy.account.position(group, timestamp) == sign * 16
    assert all(order.qty == sign and order.status is OrderStatus.PARTIALLY_FILLED for order in orders)


def test_membership_uses_identity_and_not_extra_original_state_entries():
    _, timestamp, orders, _ = setup_batch(1)
    real = orders[0]
    outsider = MarketOrder(contract=real.contract, timestamp=timestamp, qty=real.qty, time_in_force=real.time_in_force)
    assert outsider == real and outsider is not real
    trade = Trade(real.contract, outsider, timestamp, 1, 100)
    original = {id(order): (order.qty, order.status) for order in (real, outsider)}
    with pytest.raises(ValueError, match="outside the open order set"):
        validate_market_trades([trade], orders, timestamp, original)
    assert real.qty == outsider.qty == 3
