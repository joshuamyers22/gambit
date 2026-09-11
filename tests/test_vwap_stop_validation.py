from types import SimpleNamespace

import numpy as np
import pytest

from gambit.boundaries import BacktestCallbackError
from gambit.callback_contracts import validate_rule_orders
from gambit.pq_types import Contract, OrderStatus, VWAPOrder
from gambit.risk import PolicyResult
from gambit.strategy import Strategy
from gambit.strategy_components import VWAPMarketSimulator

TIMESTAMPS = np.array(["2026-09-10T10:00", "2026-09-10T10:01", "2026-09-10T10:02"],
                      dtype="datetime64[ns]")
INVALID_STOPS = [np.inf, -np.inf, True, np.bool_(False), "100", "nan", None, [100], np.array(100.), 100j]


def make_order(qty=4, **terms):
    return VWAPOrder(contract=Contract.get_or_create("VWAP-STOP"), timestamp=TIMESTAMPS[0],
                     qty=qty, vwap_end_time=TIMESTAMPS[2], **terms)


def indicators(order):
    return {order.contract.contract_group.name: SimpleNamespace(price=np.full(3, 100.), volume=np.ones(3))}


@pytest.mark.parametrize("stop", INVALID_STOPS)
@pytest.mark.parametrize("boundary", ["construction", "admission", "execution"])
def test_invalid_stop_rejected_at_each_boundary(stop, boundary):
    order = make_order()
    with pytest.raises(ValueError, match="VWAP stop must be a finite real number"):
        if boundary == "construction":
            make_order(vwap_stop=stop)
        else:
            order.vwap_stop = stop
            if boundary == "admission":
                validate_rule_orders([order], order.contract.contract_group, TIMESTAMPS[0])
            else:
                VWAPMarketSimulator("price", "volume")(
                    [order], 1, TIMESTAMPS, indicators(order), {}, SimpleNamespace())
    assert order.qty == 4 and order.status is OrderStatus.OPEN


@pytest.mark.parametrize("qty", [4, -4])
@pytest.mark.parametrize("invalid_first", [False, True])
def test_invalid_stop_preflight_prevents_partial_direct_batch_execution(qty, invalid_first):
    valid = make_order(qty)
    valid.vwap_end_time = TIMESTAMPS[0]
    invalid = make_order(qty)
    invalid.vwap_stop = np.inf
    orders = [invalid, valid] if invalid_first else [valid, invalid]
    with pytest.raises(ValueError, match="VWAP stop must be a finite real number"):
        VWAPMarketSimulator("price", "volume")(
            orders, 0, TIMESTAMPS, indicators(valid), {}, SimpleNamespace())
    assert all(order.qty == qty and order.status is OrderStatus.OPEN for order in orders)


@pytest.mark.parametrize("qty", [4, -4])
@pytest.mark.parametrize("stage", ["rule", "risk"])
@pytest.mark.parametrize("stop", [np.inf, -np.inf, True])
def test_post_construction_stop_mutation_never_reaches_accounting(qty, stage, stop):
    valid = make_order(qty)
    valid.vwap_end_time = TIMESTAMPS[0]
    invalid = make_order(qty)
    orders = [valid, invalid]
    group = valid.contract.contract_group
    strategy = Strategy(TIMESTAMPS, [group], lambda *_: 100., trade_lag=0,
                        log_orders=False, log_trades=False)
    risk_calls = []

    class Policy:
        name = "stop-probe"

        def evaluate(self, order, _context):
            risk_calls.append(order)
            if stage == "risk" and order is invalid:
                order.vwap_stop = stop
            return PolicyResult(True)

    def rule(*_args):
        if stage == "rule":
            invalid.vwap_stop = stop
        return orders

    strategy.add_risk_policy(Policy())
    strategy.add_market_sim(VWAPMarketSimulator("price", "volume"))
    strategy.indicator_values = indicators(valid)
    strategy.position_filters["test"] = None
    strategy.orders_iter = [[(rule, group, {"indicator_values": SimpleNamespace(),
                                          "signal_values": np.array([True, False, False]),
                                          "rule_name": "test"})], [], []]
    with pytest.raises(BacktestCallbackError) as error:
        strategy._run_iteration(0)
    assert "VWAP stop must be a finite real number" in str(error.value.__cause__)
    assert risk_calls == ([] if stage == "rule" else orders)
    assert len(strategy.order_decisions) == (0 if stage == "rule" else 2)
    assert all(order.qty == qty and order.status is OrderStatus.OPEN for order in orders)
    assert strategy.account.trade_count == 0 and strategy.account.symbols() == []
    assert invalid.vwap_stop is stop  # Type-specific terms are not deep-restored.


@pytest.mark.parametrize("qty", [4, -4])
@pytest.mark.parametrize("stop", [float("nan"), np.float32("nan"), np.float64("nan")])
def test_nan_remains_explicit_no_stop_at_all_boundaries(qty, stop):
    order = make_order(qty, vwap_stop=stop)
    assert validate_rule_orders([order], order.contract.contract_group, TIMESTAMPS[0])[0] is order
    simulator = VWAPMarketSimulator("price", "volume")
    assert simulator([order], 1, TIMESTAMPS, indicators(order), {}, SimpleNamespace()) == []
    assert order.qty == qty and order.status is OrderStatus.OPEN
    trades = simulator([order], 2, TIMESTAMPS, indicators(order), {}, SimpleNamespace())
    assert len(trades) == 1 and trades[0].qty == qty and trades[0].price == 100.
    assert order.status is OrderStatus.FILLED


@pytest.mark.parametrize("qty", [4, -4])
@pytest.mark.parametrize("stop", [-5., 0, 99., np.int64(100), np.float32(100), np.float64(101)])
def test_valid_stop_edits_preserve_direction_and_prorating(qty, stop):
    order = make_order(qty)
    order.vwap_stop = stop
    assert validate_rule_orders([order], order.contract.contract_group, TIMESTAMPS[0])[0] is order
    trades = VWAPMarketSimulator("price", "volume")(
        [order], 1, TIMESTAMPS, indicators(order), {}, SimpleNamespace())
    triggered = 100. <= stop if qty > 0 else 100. >= stop
    if triggered:
        assert len(trades) == 1 and trades[0].qty == qty // 2 and trades[0].price == 100.
        assert order.qty == qty // 2 and order.status is OrderStatus.CANCELLED
    else:
        assert trades == [] and order.qty == qty and order.status is OrderStatus.OPEN
