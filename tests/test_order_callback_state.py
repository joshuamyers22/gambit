from types import SimpleNamespace

import numpy as np
import pytest

from gambit.boundaries import BacktestCallbackError
from gambit.order_callback_state import OrderCallbackState
from gambit.pq_types import Contract, ContractGroup, MarketOrder, OrderStatus, TimeInForce, Trade
from gambit.risk import MaxOrderQuantity, PolicyResult, RiskContext, decide_order
from gambit.strategy import Strategy


def setup_orders(lag=0):
    group = ContractGroup.get("callback-identity")
    first = Contract.create("CALLBACK-A", group)
    second = Contract.create("CALLBACK-B", group)
    timestamps = np.array(["2026-01-02T09:30", "2026-01-02T09:31"], dtype="datetime64[ns]")
    strategy = Strategy(timestamps, [group], lambda *_: 100.0, trade_lag=lag, log_trades=False)
    order = MarketOrder(contract=first, timestamp=timestamps[0], qty=2, time_in_force=TimeInForce.GTC)
    strategy._current_orders = [order]
    return strategy, order, second


def call_rule(strategy, callback):
    strategy.position_filters["test"] = None
    return strategy._get_orders(1, callback, strategy.contract_groups[0], {
        "indicator_values": SimpleNamespace(), "signal_values": np.array([True, True]), "rule_name": "test",
    })


def mutate(order, field, second):
    values = {"contract": second, "timestamp": np.datetime64("2026-01-01", "ns"),
              "time_in_force": TimeInForce.DAY, "qty": 3, "status": OrderStatus.FILLED}
    setattr(order, field, values[field])


def assert_restored(strategy, order, contract):
    assert order.contract is contract
    assert order.timestamp == strategy.timestamps[0]
    assert order.time_in_force is TimeInForce.GTC
    assert order.qty == 2 and order.status is OrderStatus.OPEN
    assert strategy.trades() == []


@pytest.mark.parametrize("field", ["contract", "timestamp", "time_in_force"])
@pytest.mark.parametrize("with_fill", [False, True])
def test_simulator_identity_changes_are_rejected_and_restored(field, with_fill):
    strategy, order, second = setup_orders()
    original_contract = order.contract

    def simulator(*_args):
        mutate(order, field, second)
        if with_fill:
            order.fill(1)
            return [Trade(order.contract, order, strategy.timestamps[1], 1, 100)]
        return []

    strategy.add_market_sim(simulator)
    with pytest.raises(BacktestCallbackError) as error:
        strategy._sim_market(1)
    assert f"changed submitted order {field}" in str(error.value.__cause__)
    assert_restored(strategy, order, original_contract)


@pytest.mark.parametrize("field", ["contract", "timestamp", "time_in_force", "qty", "status"])
def test_rule_cannot_rewrite_pending_orders(field):
    strategy, order, second = setup_orders()
    original_contract = order.contract

    def rule(*_args):
        mutate(order, field, second)
        return []

    with pytest.raises(BacktestCallbackError):
        call_rule(strategy, rule)
    assert_restored(strategy, order, original_contract)


@pytest.mark.parametrize("field", ["contract", "timestamp", "time_in_force", "qty", "status"])
@pytest.mark.parametrize("pending", [False, True])
def test_risk_policy_cannot_rewrite_proposed_or_pending_orders(field, pending):
    strategy, order, second = setup_orders()
    original_contract = order.contract
    proposed = MarketOrder(contract=second, timestamp=strategy.timestamps[1], qty=1)
    target = order if pending else proposed
    saved = OrderCallbackState.capture(target)

    class Policy:
        name = "mutating-policy"

        def evaluate(self, _order, _context):
            mutate(target, field, original_contract if not pending else second)
            return PolicyResult(True)

    with pytest.raises(ValueError, match="callback changed"):
        decide_order(proposed, RiskContext(strategy.account, strategy.timestamps[1], [order]), [Policy()])
    saved.validate_unchanged()
    assert_restored(strategy, order, original_contract)


@pytest.mark.parametrize("stage", ["rule", "simulator", "risk"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_exception_restores_all_protected_fields(stage, interrupted):
    class Interrupt(BaseException):
        pass

    strategy, order, second = setup_orders()
    original_contract = order.contract
    exception = Interrupt if interrupted else RuntimeError

    def callback(*_args):
        for field in ("contract", "timestamp", "time_in_force", "qty", "status"):
            mutate(order, field, second)
        raise exception("callback stopped")

    expected = exception if interrupted or stage == "risk" else BacktestCallbackError
    with pytest.raises(expected):
        if stage == "rule":
            call_rule(strategy, callback)
        elif stage == "simulator":
            strategy.add_market_sim(callback)
            strategy._sim_market(1)
        else:
            decide_order(order, RiskContext(strategy.account, strategy.timestamps[1], []),
                         [SimpleNamespace(name="broken", evaluate=callback)])
    assert_restored(strategy, order, original_contract)


@pytest.mark.parametrize("cancel_method", ["request_cancel", "cancel"])
def test_successful_rule_can_still_cancel_pending_order(cancel_method):
    strategy, order, _ = setup_orders()

    def rule(*_args):
        getattr(order, cancel_method)()
        return []

    assert call_rule(strategy, rule) == []
    expected = OrderStatus.CANCEL_REQUESTED if cancel_method == "request_cancel" else OrderStatus.CANCELLED
    assert order.status is expected and order.qty == 2


@pytest.mark.parametrize("field", ["contract", "timestamp", "time_in_force", "qty", "status"])
def test_simulator_cannot_mutate_ineligible_pending_order_via_closure(field):
    strategy, eligible, second = setup_orders(lag=1)
    waiting = MarketOrder(contract=second, timestamp=strategy.timestamps[1], qty=2, time_in_force=TimeInForce.GTC)
    strategy._current_orders.append(waiting)
    saved = OrderCallbackState.capture(waiting)

    def simulator(orders, *_args):
        assert tuple(orders) == (eligible,)
        mutate(waiting, field, eligible.contract)
        return []

    strategy.add_market_sim(simulator)
    with pytest.raises(BacktestCallbackError):
        strategy._sim_market(1)
    saved.validate_unchanged()
    assert strategy._current_orders == [eligible, waiting]


def test_later_simulator_failure_preserves_prior_valid_fill():
    strategy, order, second = setup_orders()
    original_contract = order.contract

    def first(*_args):
        return [Trade(order.contract, order, strategy.timestamps[1], 1, 100)]

    def second_callback(*_args):
        order.contract = second
        order.fill()
        return []

    strategy.add_market_sim(first)
    strategy.add_market_sim(second_callback)
    with pytest.raises(BacktestCallbackError):
        strategy._sim_market(1)
    assert order.contract is original_contract
    assert order.qty == 1 and order.status is OrderStatus.PARTIALLY_FILLED
    assert [trade.qty for trade in strategy.trades()] == [1]


@pytest.mark.parametrize("field", ["contract", "timestamp", "time_in_force"])
def test_deleted_identity_fields_are_restored(field):
    strategy, order, _ = setup_orders()
    saved = OrderCallbackState.capture(order)

    def rule(*_args):
        delattr(order, field)
        return []

    with pytest.raises(BacktestCallbackError):
        call_rule(strategy, rule)
    saved.validate_unchanged()


def test_pretrade_risk_check_preserves_unspecified_submission_time():
    strategy, order, _ = setup_orders()
    order.timestamp = np.datetime64("NaT", "ns")
    decision = decide_order(order, RiskContext(strategy.account, strategy.timestamps[0], []), [MaxOrderQuantity(3)])
    assert decision.proposed_qty == 2
    assert np.isnat(order.timestamp)


@pytest.mark.parametrize("timestamp", [np.datetime64("2026-01-02T09:30", "s"),
                                       "2026-01-02T09:30", np.array([1, 2]), np.datetime64("NaT")])
def test_changed_timestamp_type_or_representation_fails_safely(timestamp):
    strategy, order, _ = setup_orders()
    saved = OrderCallbackState.capture(order)

    def rule(*_args):
        order.timestamp = timestamp
        return []

    with pytest.raises(BacktestCallbackError):
        call_rule(strategy, rule)
    saved.validate_unchanged()


def test_invalid_rule_result_rolls_back_cancellation_request():
    strategy, order, _ = setup_orders()

    def rule(*_args):
        order.request_cancel()
        return [object()]

    with pytest.raises(BacktestCallbackError):
        call_rule(strategy, rule)
    assert order.status is OrderStatus.OPEN


def test_callback_metadata_edits_are_outside_the_protected_field_set():
    strategy, order, _ = setup_orders()

    def rule(*_args):
        order.properties.note = "allowed custom state"
        return []

    assert call_rule(strategy, rule) == []
    assert order.properties.note == "allowed custom state"
