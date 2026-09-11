from types import SimpleNamespace

import numpy as np
import pytest

from gambit.pq_types import Contract, MarketOrder, OrderStatus
from gambit.risk import DecisionStatus, MaxOrderQuantity, MaxPositionQuantity, PolicyResult, RiskContext, decide_order
from gambit.strategy import Strategy

TIMESTAMP = np.datetime64("2026-09-10T10:00", "ns")
INVALID_QUANTITIES = [0, 0.5, -0.5, np.nan, np.inf, -np.inf, True, np.bool_(False), "2", [2]]


def setup_orders():
    contract = Contract.create("RISK-QUANTITY")
    other = Contract.create("OTHER-RISK-QUANTITY", contract.contract_group)
    strategy = Strategy(np.array([TIMESTAMP]), [contract.contract_group], lambda *_: 100., log_trades=False)
    order = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=2)
    pending = MarketOrder(contract=other, timestamp=TIMESTAMP, qty=3)
    context = RiskContext(strategy.account, TIMESTAMP, [pending])
    return order, pending, context


@pytest.mark.parametrize("qty", INVALID_QUANTITIES)
@pytest.mark.parametrize("with_policy", [False, True])
def test_invalid_proposal_fails_before_any_policy_even_when_none_configured(qty, with_policy):
    order, pending, context = setup_orders()
    order.qty = qty
    calls = []
    policy = SimpleNamespace(name="probe", evaluate=lambda *_: calls.append(True) or PolicyResult(True))
    with pytest.raises(ValueError, match="order qty must be finite and nonzero, in whole"):
        decide_order(order, context, [policy] if with_policy else [])
    assert calls == [] and order.qty is qty and order.status is OrderStatus.OPEN
    assert pending.qty == 3 and pending.status is OrderStatus.OPEN
    assert context.account.trade_count == 0 and context.account.symbols() == []


@pytest.mark.parametrize("qty", INVALID_QUANTITIES)
@pytest.mark.parametrize("status", [OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCEL_REQUESTED])
def test_all_live_pending_quantities_are_checked_before_policy(qty, status):
    order, pending, context = setup_orders()
    pending.qty = qty
    pending.status = status
    calls = []
    policy = SimpleNamespace(name="probe", evaluate=lambda *_: calls.append(True) or PolicyResult(True))
    with pytest.raises(ValueError, match="pending order qty must be finite and nonzero, in whole"):
        decide_order(order, context, [policy])
    # Even an unrelated instrument in the exposure context must be well formed.
    assert calls == [] and pending.qty is qty and pending.status is status
    assert order.qty == 2 and order.status is OrderStatus.OPEN
    assert context.account.trade_count == 0 and context.account.symbols() == []


def test_invalid_pending_quantity_is_checked_without_policies():
    order, pending, context = setup_orders()
    pending.qty = np.nan
    with pytest.raises(ValueError, match="pending order qty"):
        decide_order(order, context, [])


@pytest.mark.parametrize("qty", [0.5, -0.5])
def test_fractional_proposal_cannot_receive_max_quantity_acceptance(qty):
    order, _, context = setup_orders()
    order.qty = qty
    with pytest.raises(ValueError, match="order qty must be finite and nonzero, in whole"):
        decide_order(order, context, [MaxOrderQuantity(10)])


@pytest.mark.parametrize("qty", [2, -2, 2., -2., np.int64(2), np.float64(-2)])
@pytest.mark.parametrize("with_policy", [False, True])
def test_valid_numeric_edits_preserve_decision_identity_metadata_and_unscheduled_time(qty, with_policy):
    order, pending, context = setup_orders()
    order.qty = qty
    order.timestamp = np.datetime64("NaT", "ns")
    order.properties.note = "edited before pre-trade check"
    decision = decide_order(order, context, [MaxOrderQuantity(10)] if with_policy else [])
    assert decision.status is DecisionStatus.ACCEPTED and decision.order is order
    assert decision.proposed_qty is qty and decision.timestamp == TIMESTAMP
    assert order.qty is qty and np.isnat(order.timestamp)
    assert order.properties.note == "edited before pre-trade check" and pending.qty == 3


@pytest.mark.parametrize("status", [OrderStatus.FILLED, OrderStatus.CANCELLED])
def test_terminal_context_orders_may_retain_zero_remaining_quantity(status):
    order, pending, context = setup_orders()
    pending.fill()
    pending.status = status
    decision = decide_order(order, context, [MaxPositionQuantity(5)])
    assert decision.status is DecisionStatus.ACCEPTED
    assert pending.qty == 0 and pending.status is status


def test_valid_pending_partials_and_cancellation_requests_keep_position_limit_behavior():
    order, pending, context = setup_orders()
    pending.contract = order.contract
    pending.fill(1)  # Two units still pending.
    pending.request_cancel()  # Exposure remains until cancellation is acknowledged.
    accepted = decide_order(order, context, [MaxPositionQuantity(4)])
    rejected = decide_order(order, context, [MaxPositionQuantity(3)])
    assert accepted.status is DecisionStatus.ACCEPTED
    assert rejected.status is DecisionStatus.REJECTED and rejected.code == "position_quantity_exceeded"
    assert pending.qty == 2 and pending.status is OrderStatus.CANCEL_REQUESTED
    assert order.qty == 2 and order.status is OrderStatus.OPEN


def test_preflight_does_not_change_policy_order_or_first_rejection():
    order, _, context = setup_orders()
    calls = []

    def policy(name, accepted):
        def evaluate(*_args):
            calls.append(name)
            return PolicyResult(accepted, name)
        return SimpleNamespace(name=name, evaluate=evaluate)

    decision = decide_order(order, context, [policy("first", True), policy("reject", False), policy("last", True)])
    assert calls == ["first", "reject"]
    assert decision.status is DecisionStatus.REJECTED and decision.policy == decision.code == "reject"
