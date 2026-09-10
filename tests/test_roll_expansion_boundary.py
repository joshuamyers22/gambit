from types import SimpleNamespace

import numpy as np
import pytest

from gambit.boundaries import BacktestCallbackError
from gambit.callback_contracts import validate_rule_orders
from gambit.pq_types import Contract, ContractGroup, MarketOrder, OrderStatus, RollOrder, TimeInForce
from gambit.strategy import Strategy


def setup_roll(sign=1):
    group = ContractGroup.get("roll-boundary")
    outgoing = Contract.create("ROLL-OUT", group)
    incoming = Contract.create("ROLL-IN", group)
    timestamp = np.datetime64("2026-09-10T10:00", "ns")
    roll = RollOrder(contract=outgoing, reopen_contract=incoming, timestamp=timestamp,
                     close_qty=-sign * 2, reopen_qty=sign * 3)
    return group, timestamp, roll


def expand(roll, group, timestamp, boundary):
    return roll.legs() if boundary == "direct" else validate_rule_orders([roll], group, timestamp)


@pytest.mark.parametrize("boundary", ["direct", "rule"])
@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("field", ["close_qty", "reopen_qty"])
def test_mutated_same_direction_roll_is_rejected(boundary, sign, field):
    group, timestamp, roll = setup_roll(sign)
    setattr(roll, field, -getattr(roll, field))
    before = vars(roll).copy()
    with pytest.raises(ValueError, match="opposite signs"):
        expand(roll, group, timestamp, boundary)
    assert vars(roll) == before


@pytest.mark.parametrize("boundary", ["direct", "rule"])
@pytest.mark.parametrize("field", ["close_qty", "reopen_qty"])
@pytest.mark.parametrize("value", [0, 0.5, np.nan, np.inf, True, "2"])
def test_mutated_roll_quantities_are_revalidated(boundary, field, value):
    group, timestamp, roll = setup_roll()
    setattr(roll, field, value)
    with pytest.raises(ValueError, match="whole shares or contracts"):
        expand(roll, group, timestamp, boundary)


@pytest.mark.parametrize("boundary", ["direct", "rule"])
def test_mutated_same_contract_roll_is_rejected(boundary):
    group, timestamp, roll = setup_roll()
    roll.reopen_contract = roll.contract
    with pytest.raises(ValueError, match="distinct"):
        expand(roll, group, timestamp, boundary)


def test_direct_expansion_rechecks_contract_group():
    _, _, roll = setup_roll()
    roll.reopen_contract = Contract.create("ROLL-OTHER", ContractGroup.get("other"))
    with pytest.raises(ValueError, match="same contract group"):
        roll.legs()


@pytest.mark.parametrize("field", ["contract", "reopen_contract"])
def test_direct_expansion_rechecks_contract_types(field):
    _, _, roll = setup_roll()
    setattr(roll, field, object())
    with pytest.raises(TypeError, match="contract must be a Contract"):
        roll.legs()


@pytest.mark.parametrize("boundary", ["direct", "rule"])
@pytest.mark.parametrize("status", [OrderStatus.CANCEL_REQUESTED, OrderStatus.CANCELLED,
                                    OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, "OPEN"])
def test_roll_expansion_cannot_reset_lifecycle(boundary, status):
    group, timestamp, roll = setup_roll()
    roll.status = status
    with pytest.raises(ValueError, match="only an open roll"):
        expand(roll, group, timestamp, boundary)
    assert roll.status is status


@pytest.mark.parametrize("sign", [1, -1])
def test_valid_rebalanced_roll_keeps_leg_order_and_source_state(sign):
    group, timestamp, roll = setup_roll(sign)
    roll.close_qty = np.float64(-sign * 4)
    roll.reopen_qty = np.int64(sign * 5)
    roll.properties = SimpleNamespace(note="rebalance")
    before = vars(roll).copy()
    close, reopen = validate_rule_orders([roll], group, timestamp)
    assert close.contract is roll.contract and reopen.contract is roll.reopen_contract
    assert [close.qty, reopen.qty] == [-sign * 4, sign * 5]
    assert close.properties.note == reopen.properties.note == "rebalance"
    assert close.properties._gambit_roll_id == reopen.properties._gambit_roll_id
    assert close.status is reopen.status is OrderStatus.OPEN
    assert vars(roll) == before


def test_invalid_roll_rejects_whole_rule_batch_before_risk_and_accounting():
    group, timestamp, roll = setup_roll()
    strategy = Strategy(np.array([timestamp]), [group], lambda *_: 100.0, trade_lag=0,
                        log_orders=False, log_trades=False)
    pending = MarketOrder(contract=roll.contract, timestamp=timestamp, qty=1, time_in_force=TimeInForce.GTC)
    strategy._current_orders = [pending]
    calls = []

    def rule(*_args):
        pending.request_cancel()
        roll.reopen_qty = roll.close_qty
        return [MarketOrder(contract=roll.contract, timestamp=timestamp, qty=1), roll]

    strategy.add_risk_policy(SimpleNamespace(name="probe", evaluate=lambda *_: calls.append("risk")))
    strategy.position_filters["test"] = None
    strategy.orders_iter = [[(rule, group, {"indicator_values": SimpleNamespace(),
                                          "signal_values": np.array([True]), "rule_name": "test"})]]
    with pytest.raises(BacktestCallbackError) as error:
        strategy._run_iteration(0)
    assert "opposite signs" in str(error.value.__cause__)
    assert calls == [] and strategy.order_decisions == []
    assert strategy._orders == [] and strategy._current_orders == [pending]
    assert pending.status is OrderStatus.OPEN and pending.qty == 1
    assert strategy.account.trade_count == 0 and strategy.account.symbols() == []
