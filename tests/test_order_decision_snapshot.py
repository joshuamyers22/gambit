from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from gambit.backtest_result import BacktestResult
from gambit.pq_types import Contract, ContractGroup, LimitOrder, MarketOrder, RollOrder, TimeInForce, VWAPOrder
from gambit.risk import MaxOrderQuantity, RiskContext, decide_order
from gambit.strategy import Strategy

TIMESTAMP = np.datetime64("2026-09-11T10:00", "ns")


@pytest.mark.parametrize("reject", [False, True])
@pytest.mark.parametrize("lifecycle", ["partial", "filled", "cancelled"])
def test_decision_snapshot_detaches_order_identity_terms_and_lifecycle(reject, lifecycle):
    contract = Contract.create("AUDIT", ContractGroup.get("audit-group"), multiplier=10.)
    order = LimitOrder(contract=contract, timestamp=TIMESTAMP, qty=4, limit_price=101.,
                       time_in_force=TimeInForce.GTC, reason_code="entry")
    strategy = Strategy(np.array([TIMESTAMP]), [contract.contract_group], lambda *_: 100.)
    decision = decide_order(order, RiskContext(strategy.account, TIMESTAMP, []),
                            [MaxOrderQuantity(1)] if reject else [])
    if lifecycle == "partial":
        order.fill(1)
    elif lifecycle == "filled":
        order.fill()
    else:
        order.request_cancel()
        order.cancel()
    order.contract = Contract.create("REPLACEMENT")
    order.timestamp += np.timedelta64(1, "D")
    order.time_in_force = TimeInForce.DAY
    order.limit_price = 999.
    order.reason_code = "changed"
    contract.contract_group._name = "MUTATED-GROUP"

    assert decision.order is order  # Compatibility reference is intentionally live.
    snapshot = decision.snapshot
    assert snapshot.symbol == "AUDIT"
    assert snapshot.contract_group != "MUTATED-GROUP"
    assert snapshot.multiplier == 10.
    assert snapshot.submitted_at == TIMESTAMP
    assert snapshot.qty == 4
    assert snapshot.order_type == "LimitOrder"
    assert snapshot.time_in_force == "GTC"
    assert snapshot.order_status == "open"
    assert snapshot.reason_code == "entry"
    assert snapshot.limit_price == 101.
    with pytest.raises(FrozenInstanceError):
        snapshot.symbol = "change"


def test_vwap_terms_are_detached():
    contract = Contract.create("AUDIT-VWAP")
    end = TIMESTAMP + np.timedelta64(1, "h")
    order = VWAPOrder(contract=contract, timestamp=TIMESTAMP, qty=2, vwap_end_time=end, vwap_stop=103.)
    strategy = Strategy(np.array([TIMESTAMP]), [contract.contract_group], lambda *_: 100.)
    decision = decide_order(order, RiskContext(strategy.account, TIMESTAMP, []), [])
    order.vwap_stop = 999.
    order.vwap_end_time += np.timedelta64(1, "D")
    assert decision.snapshot.vwap_stop == 103.
    assert decision.snapshot.vwap_end_time == end


def test_roll_command_and_expanded_leg_terms_are_detached():
    outgoing = Contract.create("AUDIT-OLD")
    incoming = Contract.create("AUDIT-NEXT", outgoing.contract_group)
    order = RollOrder(contract=outgoing, reopen_contract=incoming, timestamp=TIMESTAMP, close_qty=-2, reopen_qty=2)
    strategy = Strategy(np.array([TIMESTAMP]), [outgoing.contract_group], lambda *_: 100.)
    context = RiskContext(strategy.account, TIMESTAMP, [])
    decision = decide_order(order, context, [])
    legs = [decide_order(leg, context, []) for leg in order.legs()]
    order.reopen_contract = Contract.create("MUTATED", outgoing.contract_group)
    order.close_qty = -99
    order.reopen_qty = 99
    assert decision.snapshot.reopen_symbol == "AUDIT-NEXT"
    assert decision.snapshot.close_qty == -2 and decision.snapshot.reopen_qty == 2
    assert [leg.snapshot.roll_leg for leg in legs] == ["close", "reopen"]
    assert legs[0].snapshot.roll_id == legs[1].snapshot.roll_id
    legs[0].order.properties._gambit_roll_id = "changed"
    assert legs[0].snapshot.roll_id == legs[1].snapshot.roll_id


def test_repeated_standalone_decisions_capture_each_proposal_separately():
    contract = Contract.create("AUDIT-REPEAT")
    order = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=2)
    strategy = Strategy(np.array([TIMESTAMP]), [contract.contract_group], lambda *_: 100.)
    context = RiskContext(strategy.account, TIMESTAMP, [])
    first = decide_order(order, context, [])
    order.qty = 3
    second = decide_order(order, context, [])
    assert first.snapshot.qty == 2 and second.snapshot.qty == 3


@pytest.mark.parametrize("kind", [LimitOrder, VWAPOrder, RollOrder])
def test_builtin_terms_survive_strategy_result_round_trip(tmp_path, kind):
    contract = Contract.create("AUDIT-TERMS", expiry=TIMESTAMP + np.timedelta64(1, "D"))
    if kind is LimitOrder:
        order = kind(contract=contract, timestamp=TIMESTAMP, qty=2, limit_price=101.)
    elif kind is VWAPOrder:
        order = kind(contract=contract, timestamp=TIMESTAMP, qty=2, vwap_stop=102.,
                     vwap_end_time=TIMESTAMP + np.timedelta64(1, "h"))
    else:
        incoming = Contract.create("AUDIT-TERMS-NEXT", contract.contract_group)
        order = kind(contract=contract, reopen_contract=incoming, timestamp=TIMESTAMP, close_qty=-2, reopen_qty=2)
    strategy = Strategy(np.array([TIMESTAMP]), [contract.contract_group], lambda *_: 100., run_final_calc=False)
    strategy.add_signal("entry", lambda *_: np.array([True]))
    strategy.add_rule("enter", lambda *_: [order], "entry")
    result = strategy.run()
    restored = BacktestResult.load(result.save(tmp_path / "terms"))
    assert restored.decisions.equals(result.decisions)
    assert restored.decisions["expiry"][0] is not None
    if kind is LimitOrder:
        assert restored.decisions["limit_price"].to_list() == [101.]
    elif kind is VWAPOrder:
        assert restored.decisions["vwap_stop"].to_list() == [102.]
        assert restored.decisions["vwap_end_time"][0] is not None
    else:
        assert restored.decisions["roll_leg"].to_list() == ["close", "reopen"]
        assert restored.decisions["roll_id"].n_unique() == 1
