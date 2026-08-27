from types import SimpleNamespace

import numpy as np

from gambit.pq_types import Contract, ContractGroup, MarketOrder, OrderStatus
from gambit.risk import DecisionStatus, MaxOrderQuantity, MaxPositionQuantity, RiskContext, decide_order
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator


def _price(_contract, _timestamps, _index, _context):
    return 100.0


def test_max_order_quantity_produces_auditable_rejection() -> None:
    group = ContractGroup.get("order-risk")
    contract = Contract.create("ORDER-RISK", group)
    timestamp = np.datetime64("2024-01-02")
    strategy = Strategy(np.array([timestamp]), [group], _price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=11)
    context = RiskContext(strategy.account, timestamp, [])

    decision = decide_order(order, context, [MaxOrderQuantity(10)])

    assert decision.status is DecisionStatus.REJECTED
    assert decision.code == "order_quantity_exceeded"
    assert decision.proposed_qty == 11


def test_position_policy_includes_pending_orders() -> None:
    group = ContractGroup.get("position-risk")
    contract = Contract.create("POSITION-RISK", group)
    timestamp = np.datetime64("2024-01-02")
    strategy = Strategy(np.array([timestamp]), [group], _price)
    pending = MarketOrder(contract=contract, timestamp=timestamp, qty=4)
    proposed = MarketOrder(contract=contract, timestamp=timestamp, qty=2)

    decision = decide_order(proposed, RiskContext(strategy.account, timestamp, [pending]), [MaxPositionQuantity(5)])

    assert decision.status is DecisionStatus.REJECTED
    assert decision.code == "position_quantity_exceeded"


def test_position_policy_allows_order_that_reduces_an_existing_breach() -> None:
    group = ContractGroup.get("reduce-risk")
    contract = Contract.create("REDUCE-RISK", group)
    timestamp = np.datetime64("2024-01-02")
    strategy = Strategy(np.array([timestamp]), [group], _price)
    pending = MarketOrder(contract=contract, timestamp=timestamp, qty=10)
    proposed = MarketOrder(contract=contract, timestamp=timestamp, qty=-2)

    decision = decide_order(proposed, RiskContext(strategy.account, timestamp, [pending]), [MaxPositionQuantity(5)])

    assert decision.status is DecisionStatus.ACCEPTED


def test_strategy_rejects_order_before_market_simulation() -> None:
    group = ContractGroup.get("strategy-risk")
    contract = Contract.create("STRATEGY-RISK", group)
    timestamp = np.datetime64("2024-01-02")
    strategy = Strategy(np.array([timestamp]), [group], _price, trade_lag=0)
    strategy.add_risk_policy(MaxOrderQuantity(5))
    strategy.add_market_sim(SimpleMarketSimulator(_price))
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=6)

    strategy.orders_iter = [[(lambda *_args: [order], group, {
        "indicator_values": SimpleNamespace(),
        "signal_values": np.array([True]),
        "rule_name": "entry",
    })]]
    strategy.position_filters["entry"] = None
    strategy._run_iteration(0)

    assert order.status is OrderStatus.CANCELLED
    assert strategy.trades() == []
    assert strategy.order_decisions[0].status is DecisionStatus.REJECTED
