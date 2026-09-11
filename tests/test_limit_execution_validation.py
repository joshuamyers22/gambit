from types import SimpleNamespace

import numpy as np
import pytest

from gambit.boundaries import BacktestCallbackError
from gambit.pq_types import Contract, LimitOrder, MarketOrder, OrderStatus
from gambit.risk import PolicyResult
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator

TIMESTAMP = np.datetime64("2026-09-10T10:00", "ns")
TIMESTAMPS = np.array([TIMESTAMP])


def make_order(qty):
    return LimitOrder(contract=Contract.create("EXECUTION-LIMIT"), timestamp=TIMESTAMP, qty=qty, limit_price=50.0)


def run(simulator, orders):
    return simulator(orders, 0, TIMESTAMPS, {}, {}, SimpleNamespace())


@pytest.mark.parametrize("qty", [2, -2])
@pytest.mark.parametrize("limit", [np.nan, np.inf, -np.inf, True, np.bool_(False), "50", [50], None, 50j])
def test_direct_execution_rechecks_mutated_limit(qty, limit):
    order = make_order(qty)
    order.limit_price = limit
    calls = []
    charge = SimpleNamespace(charge=lambda *_: calls.append("charge") or 0.0)
    simulator = SimpleMarketSimulator(lambda *_: 100.0, commission_model=charge, fee_model=charge,
                                      post_trade_func=lambda *_: calls.append("post-trade"))
    with pytest.raises(ValueError, match="limit price must be a finite real number"):
        run(simulator, [order])
    assert order.qty == qty and order.status is OrderStatus.OPEN
    assert calls == []


@pytest.mark.parametrize("qty", [2, -2])
@pytest.mark.parametrize("limit", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("stage", ["price", "slippage"])
def test_limit_is_checked_after_price_and_slippage_callbacks(qty, limit, stage):
    order = make_order(qty)

    def price(*_args):
        if stage == "price":
            order.limit_price = limit
        return 100.0

    def slippage(*_args):
        if stage == "slippage":
            order.limit_price = limit
        return 0.0

    simulator = SimpleMarketSimulator(price, slippage_model=SimpleNamespace(adjustment=slippage))
    with pytest.raises(ValueError, match="limit price must be a finite real number"):
        run(simulator, [order])
    assert order.qty == qty and order.status is OrderStatus.OPEN


@pytest.mark.parametrize("qty", [2, -2])
@pytest.mark.parametrize("limit", [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize("invalid_first", [False, True])
def test_risk_mutation_fails_execution_batch_without_accounting(qty, limit, invalid_first):
    invalid = make_order(qty)
    valid = MarketOrder(contract=invalid.contract, timestamp=TIMESTAMP, qty=qty)
    orders = [invalid, valid] if invalid_first else [valid, invalid]
    group = invalid.contract.contract_group
    strategy = Strategy(TIMESTAMPS, [group], lambda *_: 100.0, trade_lag=0,
                        log_orders=False, log_trades=False)
    decisions = []
    post_trades = []

    class Policy:
        name = "mutate-limit-after-admission"

        def evaluate(self, order, _context):
            decisions.append(order)
            if order is invalid:
                order.limit_price = limit
            return PolicyResult(True)

    strategy.add_risk_policy(Policy())
    strategy.add_market_sim(SimpleMarketSimulator(lambda *_: 100.0,
                                                  post_trade_func=lambda *_: post_trades.append(True)))
    strategy.position_filters["test"] = None
    strategy.orders_iter = [[(lambda *_: orders, group, {"indicator_values": SimpleNamespace(),
                                                       "signal_values": np.array([True]), "rule_name": "test"})]]
    with pytest.raises(BacktestCallbackError) as error:
        strategy._run_iteration(0)
    assert "limit price must be a finite real number" in str(error.value.__cause__)
    assert decisions == orders and len(strategy.order_decisions) == 2
    assert all(order.qty == qty and order.status is OrderStatus.OPEN for order in orders)
    assert post_trades == []
    assert strategy.account.trade_count == 0 and strategy.account.symbols() == []
    # Type-specific terms remain mutable and are not part of callback rollback.
    assert invalid.limit_price is limit


@pytest.mark.parametrize("qty,price,limit,filled", [
    (2, -10.0, -10.0, True), (-2, -10.0, -10.0, True),
    (2, -9.0, -10.0, False), (-2, -11.0, -10.0, False),
    (2, 0.0, 0, True), (-2, 0.0, 0, True),
    (2, 1.0, 0, False), (-2, -1.0, 0, False),
    (2, 99.0, np.float64(100.0), True), (-2, 101.0, np.int64(100), True),
])
def test_valid_signed_and_numpy_limits_preserve_marketability(qty, price, limit, filled):
    order = make_order(qty)
    order.limit_price = limit
    trades = run(SimpleMarketSimulator(lambda *_: price), [order])
    assert len(trades) == int(filled)
    assert order.status is (OrderStatus.FILLED if filled else OrderStatus.OPEN)
    assert order.qty == (0 if filled else qty)
    if filled:
        assert trades[0].qty == qty and trades[0].price == price
