"""Monetary sizing and order-cardinality regressions for public entry rules."""

from types import SimpleNamespace

import numpy as np
import pytest

from gambit.account import Account
from gambit.pq_types import Contract, ContractGroup
from gambit.strategy_components import (
    BracketOrderEntryRule,
    PercentOfEquityTradingRule,
    VWAPEntryRule,
    VWAPMarketSimulator,
)

TIMESTAMPS = np.array(["2026-09-04T09:30", "2026-09-04T09:35"], dtype="datetime64[ns]")


def price(*_args):
    return 100.0


def run_rule(rule, *, multipliers=(50,), equity=100_000.0, indicators=None):
    group = ContractGroup.get("entry")
    for index, multiplier in enumerate(multipliers):
        Contract.create(f"CONTRACT-{index}", group, multiplier=multiplier)
    account = Account([group], TIMESTAMPS, price, None, starting_equity=equity)
    orders = rule(group, 0, TIMESTAMPS, indicators or SimpleNamespace(), np.array([1, 0]), account, (), SimpleNamespace())
    return orders, account, group


@pytest.mark.parametrize("multiplier", [1, 50])
@pytest.mark.parametrize("long", [True, False])
def test_equity_allocation_uses_monetary_contract_notional(multiplier, long):
    orders, _, _ = run_rule(PercentOfEquityTradingRule("entry", price, long=long), multipliers=(multiplier,))
    assert orders[0].qty == (1 if long else -1) * (100 // multiplier)
    assert abs(orders[0].qty * 100 * multiplier) == 10_000


def test_equity_allocation_splits_budget_across_contracts():
    orders, _, _ = run_rule(PercentOfEquityTradingRule("entry", price, allocate_risk=True), multipliers=(1, 50))
    assert [order.qty for order in orders] == [50, 1]
    assert sum(order.qty * 100 * order.contract.multiplier for order in orders) == 10_000


@pytest.mark.parametrize("long", [True, False])
@pytest.mark.parametrize("cap, expected", [(0.0, 20), (0.1, 2)])
def test_bracket_stop_risk_and_position_cap_use_contract_multiplier(long, cap, expected):
    rule = BracketOrderEntryRule("entry", price, long=long, stop_return_func=lambda *_: -0.1, max_position_size=cap)
    orders, _, _ = run_rule(rule)
    assert orders[0].qty == (1 if long else -1) * expected
    assert abs(orders[0].qty) * 10 * 50 <= 10_000
    if cap:
        assert abs(orders[0].qty) * 100 * 50 <= 100_000 * cap


@pytest.mark.parametrize("long, stop", [(True, 90.0), (False, 110.0)])
def test_vwap_returns_every_contract_and_respects_shared_stop_budget(long, stop):
    rule = VWAPEntryRule("entry", 5, price, long=long, stop_price_ind="stop")
    orders, account, group = run_rule(rule, multipliers=(1, 50), indicators=SimpleNamespace(stop=np.array([stop, stop])))
    sign = 1 if long else -1
    assert [(order.contract.symbol, order.qty) for order in orders] == [("CONTRACT-0", sign * 500), ("CONTRACT-1", sign * 10)]
    assert sum(abs(order.qty) * abs(100 - stop) * order.contract.multiplier for order in orders) == 10_000

    simulator = VWAPMarketSimulator("price", "volume")
    trades = simulator(orders, 1, TIMESTAMPS, {group.name: SimpleNamespace(price=np.array([100., 100.]), volume=np.array([10., 10.]))}, {}, SimpleNamespace())
    account.add_trades(trades)
    assert [(contract.symbol, qty) for contract, qty in account.positions(group, TIMESTAMPS[1])] == [("CONTRACT-0", sign * 500), ("CONTRACT-1", sign * 10)]
    assert account.equity(TIMESTAMPS[1]) == 100_000


@pytest.mark.parametrize("long", [True, False])
def test_vwap_without_stop_uses_allocation_sizing(long):
    orders, _, _ = run_rule(VWAPEntryRule("entry", 5, price, long=long))
    assert orders[0].qty == (2 if long else -2)
    assert np.isnan(orders[0].vwap_stop)
    assert orders[0].vwap_end_time == TIMESTAMPS[1]


def test_vwap_empty_group_returns_no_orders():
    orders, _, _ = run_rule(VWAPEntryRule("entry", 5, price), multipliers=())
    assert orders == []


@pytest.mark.parametrize("rule", [PercentOfEquityTradingRule("entry", price, allocate_risk=True), VWAPEntryRule("entry", 5, price)])
def test_unaffordable_contract_does_not_discard_other_entries(rule):
    orders, _, _ = run_rule(rule, multipliers=(1, 1000))
    assert [(order.contract.symbol, order.qty) for order in orders] == [("CONTRACT-0", 50)]


@pytest.mark.parametrize("stop", [100.0, 110.0, float("inf"), float("nan")])
def test_vwap_rejects_invalid_long_stop_distance(stop):
    with pytest.raises(ValueError, match="stop"):
        run_rule(VWAPEntryRule("entry", 5, price, stop_price_ind="stop"), indicators=SimpleNamespace(stop=np.array([stop, stop])))


@pytest.mark.parametrize("factory", [
    lambda p: PercentOfEquityTradingRule("entry", p),
    lambda p: VWAPEntryRule("entry", 5, p),
    lambda p: BracketOrderEntryRule("entry", p),
])
@pytest.mark.parametrize("value", [0.0, -100.0, float("inf")])
def test_entry_rules_reject_unusable_allocation_prices(factory, value):
    with pytest.raises(ValueError, match="price"):
        run_rule(factory(lambda *_: value))


@pytest.mark.parametrize("factory", [
    lambda fraction: PercentOfEquityTradingRule("entry", price, equity_percent=fraction),
    lambda fraction: VWAPEntryRule("entry", 5, price, percent_of_equity=fraction),
    lambda fraction: BracketOrderEntryRule("entry", price, percent_of_equity=fraction),
])
@pytest.mark.parametrize("fraction", [-0.1, float("nan"), float("inf")])
def test_entry_rules_reject_invalid_equity_fractions(factory, fraction):
    with pytest.raises(ValueError, match="equity"):
        factory(fraction)
