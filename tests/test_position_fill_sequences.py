"""Position admission must be safe without assuming pending fill order."""

from itertools import permutations, product
from types import SimpleNamespace

import numpy as np
import pytest

from gambit.pq_types import Contract, MarketOrder, OrderStatus, RollOrder, TimeInForce, Trade
from gambit.risk import DecisionStatus, MaxPositionQuantity, RiskContext, decide_order
from gambit.strategy import Strategy

TIMESTAMP = np.datetime64("2026-09-11T10:00", "ns")


def setup_position(current=0):
    contract = Contract.create("FILL-SEQUENCE")
    strategy = Strategy(np.array([TIMESTAMP]), [contract.contract_group], lambda *_: 100., log_trades=False)
    if current:
        seed = make_order(contract, current)
        strategy.account.add_trades([Trade(contract, seed, TIMESTAMP, current, 100.)])
        seed.fill()
    return strategy, contract


def make_order(contract, qty):
    return MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=qty, time_in_force=TimeInForce.GTC)


@pytest.mark.parametrize("sign", [-1, 1])
def test_opposite_pending_order_cannot_offset_proposal(sign):
    strategy, contract = setup_position()
    pending = make_order(contract, -100 * sign)
    proposed = make_order(contract, 200 * sign)
    decision = decide_order(proposed, RiskContext(strategy.account, TIMESTAMP, [pending]), [MaxPositionQuantity(100)])
    assert decision.status is DecisionStatus.REJECTED
    assert decision.code == "position_quantity_exceeded"


@pytest.mark.parametrize("current,qty,accepted", [
    (10, -2, True), (-10, 2, True), (10, 1, False), (-10, -1, False),
    (10, -15, True), (-10, 15, True), (10, -16, False), (-10, 16, False),
])
def test_existing_breach_may_reduce_but_not_create_opposite_breach(current, qty, accepted):
    strategy, contract = setup_position(current)
    decision = decide_order(make_order(contract, qty), RiskContext(strategy.account, TIMESTAMP, []),
                            [MaxPositionQuantity(5)])
    assert (decision.status is DecisionStatus.ACCEPTED) == accepted


@pytest.mark.parametrize("status,accepted", [
    (OrderStatus.OPEN, False), (OrderStatus.PARTIALLY_FILLED, False),
    (OrderStatus.CANCEL_REQUESTED, False), (OrderStatus.CANCELLED, True), (OrderStatus.FILLED, True),
])
def test_pending_lifecycle_controls_release_of_reserved_exposure(status, accepted):
    strategy, contract = setup_position()
    pending = make_order(contract, 4)
    pending.status = status
    decision = decide_order(make_order(contract, 2), RiskContext(strategy.account, TIMESTAMP, [pending]),
                            [MaxPositionQuantity(5)])
    assert (decision.status is DecisionStatus.ACCEPTED) == accepted


def test_partial_fill_uses_account_position_and_only_remaining_quantity():
    strategy, contract = setup_position()
    pending = make_order(contract, 5)
    strategy.account.add_trades([Trade(contract, pending, TIMESTAMP, 2, 100.)])
    pending.fill(2)
    context = RiskContext(strategy.account, TIMESTAMP, [pending])
    assert decide_order(make_order(contract, 1), context, [MaxPositionQuantity(5)]).status is DecisionStatus.REJECTED
    assert decide_order(make_order(contract, -5), context, [MaxPositionQuantity(5)]).status is DecisionStatus.ACCEPTED


def test_pending_other_instrument_does_not_offset_or_consume_limit():
    strategy, contract = setup_position()
    other = Contract.create("OTHER-FILL-SEQUENCE", contract.contract_group)
    context = RiskContext(strategy.account, TIMESTAMP, [make_order(other, -100)])
    assert decide_order(make_order(contract, 6), context, [MaxPositionQuantity(5)]).status is DecisionStatus.REJECTED
    assert decide_order(make_order(contract, -5), context, [MaxPositionQuantity(5)]).status is DecisionStatus.ACCEPTED


@pytest.mark.parametrize("current", [-2, 0, 2])
def test_admission_matches_exhaustive_partial_fill_oracle(current):
    strategy, contract = setup_position(current)
    for pending_qty in product([-2, 2], repeat=2):
        pending = [make_order(contract, qty) for qty in pending_qty]
        for proposed_qty in [-3, -1, 1, 3]:
            quantities = (*pending_qty, proposed_qty)
            fills = [range(min(0, qty), max(0, qty) + 1) for qty in quantities]
            safe = all(abs(current + sum(partial)) <= 6 for partial in product(*fills))
            decision = decide_order(make_order(contract, proposed_qty),
                                    RiskContext(strategy.account, TIMESTAMP, pending), [MaxPositionQuantity(6)])
            assert (decision.status is DecisionStatus.ACCEPTED) == safe


@pytest.mark.parametrize("fill_order", list(permutations(range(3))))
def test_strategy_keeps_every_partial_fill_sequence_within_limit(fill_order):
    strategy, contract = setup_position()
    orders = [make_order(contract, qty) for qty in [-5, 5, 5, 1]]
    strategy.add_risk_policy(MaxPositionQuantity(10))
    strategy.position_filters["test"] = None
    strategy.orders_iter = [[(lambda *_: orders, contract.contract_group, {
        "indicator_values": SimpleNamespace(), "signal_values": np.array([True]), "rule_name": "test",
    })]]
    strategy._run_iteration(0)
    assert [d.status for d in strategy.order_decisions] == [DecisionStatus.ACCEPTED] * 3 + [DecisionStatus.REJECTED]
    assert orders[-1].status is OrderStatus.CANCELLED
    observed = []

    def simulator(pending, index, timestamps, *_):
        order = next(orders[i] for i in fill_order if orders[i].is_open())
        assert any(item is order for item in pending)
        qty = 1 if order.qty > 0 else -1
        trade = Trade(contract, order, timestamps[index], qty, 100.)
        order.fill(qty)
        return [trade]

    strategy.add_market_sim(simulator)
    for _ in range(15):
        strategy._sim_market(0)
        position = sum(qty for _, qty in strategy.account.positions(contract.contract_group, TIMESTAMP))
        observed.append(position)
    assert all(abs(position) <= 10 for position in observed)
    assert observed[-1] == 5


def test_roll_legs_do_not_offset_pending_in_the_reopen_contract():
    strategy, outgoing = setup_position(5)
    incoming = Contract.create("FILL-SEQUENCE-NEXT", outgoing.contract_group)
    pending = make_order(incoming, -5)
    roll = RollOrder(contract=outgoing, reopen_contract=incoming, timestamp=TIMESTAMP, close_qty=-5, reopen_qty=10)
    strategy._current_orders = [pending]
    strategy.add_risk_policy(MaxPositionQuantity(5))
    strategy.position_filters["test"] = None
    strategy.orders_iter = [[(lambda *_: [roll], outgoing.contract_group, {
        "indicator_values": SimpleNamespace(), "signal_values": np.array([True]), "rule_name": "test",
    })]]
    strategy._run_iteration(0)
    assert [d.status for d in strategy.order_decisions] == [DecisionStatus.ACCEPTED, DecisionStatus.REJECTED]
