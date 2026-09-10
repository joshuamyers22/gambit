from types import SimpleNamespace

import numpy as np
import pytest

from gambit.portfolio import Portfolio
from gambit.pq_types import Contract, ContractGroup, MarketOrder
from gambit.sparse_iterations import SparseIterations
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator


def price(*_args):
    return 100.0


def make_strategy(*, lag=0, count=19, dense=False):
    groups = [ContractGroup.get("sparse-a"), ContractGroup.get("sparse-b")]
    timestamps = np.datetime64("2024-01-02T09:30", "ns") + np.arange(count) * np.timedelta64(1, "m")
    strategy = Strategy(timestamps, groups, price, trade_lag=lag, log_trades=False)
    calls = []

    def signal(*_args):
        return np.ones(count) if dense else np.where(np.arange(count) % 7 == 1, 1.0, 0.0)

    def first(group, index, *_args):
        calls.append((index, "first", group.name))
        return []

    def second(group, index, *_args):
        calls.append((index, "second", group.name))
        return []

    strategy.add_signal("trigger", signal)
    strategy.add_rule("first", first, "trigger")
    strategy.add_rule("second", second, "trigger")
    strategy.run_indicators()
    strategy.run_signals()
    return strategy, calls


@pytest.mark.parametrize("lag", [0, 1, 3])
@pytest.mark.parametrize("dense", [False, True])
def test_sparse_rule_schedule_matches_dense_reference_order(lag, dense):
    strategy, calls = make_strategy(lag=lag, dense=dense)
    expected = []
    for index in range(len(strategy.timestamps)):
        if lag and index == len(strategy.timestamps) - 1:
            continue
        for rule_name in strategy.rule_names:
            for group in strategy.contract_groups:
                values = strategy.signal_values[group.name].trigger
                if values[index] == 1:
                    expected.append((index, rule_name, group.name))
    strategy.run_rules()
    assert calls == expected
    assert isinstance(strategy.orders_iter, SparseIterations)
    assert strategy.orders_iter.populated_count == len({item[0] for item in expected})
    assert len(strategy.orders_iter) == len(strategy.timestamps)
    assert strategy.trades_iter.populated_count == 0


def test_empty_schedule_and_debug_reads_do_not_retain_buckets():
    strategy, _ = make_strategy()
    for group in strategy.contract_groups:
        strategy.signal_values[group.name] = SimpleNamespace(trigger=np.zeros(len(strategy.timestamps)))
    strategy.run_rules()
    assert strategy.orders_iter.populated_count == 0
    assert all(bucket == [] for bucket in strategy.orders_iter)
    assert all(bucket == [] for bucket in strategy.trades_iter)
    assert strategy.orders_iter.populated_count == strategy.trades_iter.populated_count == 0


def test_schedule_keeps_independent_signal_copies_and_selected_registration_order():
    strategy, calls = make_strategy(dense=True)
    groups = list(reversed(strategy.contract_groups))
    strategy._generate_order_iterations(["second", "first"], groups)
    for group in groups:
        strategy.signal_values[group.name].trigger = np.zeros_like(strategy.signal_values[group.name].trigger)
    assert [item[2]["signal_values"][0] for item in strategy.orders_iter[0]] == [1, 1, 1, 1]
    strategy._run_iteration(0)
    assert calls == [(0, name, group.name) for name in ["second", "first"] for group in groups]


def test_schedule_regeneration_discards_previous_entries():
    strategy, _ = make_strategy(dense=True)
    strategy._generate_order_iterations()
    previous = strategy.orders_iter
    strategy._generate_order_iterations(rule_names=[])
    assert strategy.orders_iter.populated_count == 0
    assert previous.populated_count == len(strategy.timestamps)


def test_date_window_preserves_existing_callback_signal_masking():
    strategy, calls = make_strategy(dense=True)
    strategy.run_rules(start_date=strategy.timestamps[3], end_date=strategy.timestamps[6])
    # Existing scheduler masks the signal at the exact end boundary; do not
    # silently change that policy while replacing the storage representation.
    assert sorted({index for index, _, _ in calls}) == [3, 4, 5]
    assert strategy.orders_iter.populated_count == 3


def test_portfolio_executes_sparse_buckets_in_the_same_order():
    strategy, calls = make_strategy()
    portfolio = Portfolio()
    portfolio.add_strategy("sparse", strategy)
    portfolio.run_rules()
    assert calls == [(index, name, group.name) for index in [1, 8, 15]
                     for name in strategy.rule_names for group in strategy.contract_groups]


@pytest.mark.parametrize("lag", [1, 3])
def test_pending_order_fills_on_timestamp_without_scheduled_rules(lag):
    strategy, _ = make_strategy(lag=lag, count=7)
    group = strategy.contract_groups[0]
    contract = Contract.create("SPARSE-PENDING", group)

    def entry(_group, index, timestamps, *_args):
        return [MarketOrder(contract=contract, timestamp=timestamps[index], qty=2)]

    strategy.add_rule("entry", entry, "trigger")
    strategy.add_market_sim(SimpleMarketSimulator(price))
    strategy.run_rules(rule_names=["entry"], contract_groups=[group])

    assert strategy.orders_iter.populated_count == 1
    assert strategy.orders_iter[1 + lag] == []
    trades = strategy.trades()
    assert len(trades) == 1
    assert trades[0].timestamp == strategy.timestamps[1 + lag]
    assert trades[0].qty == 2
    assert strategy.account.position(group, strategy.timestamps[-1]) == 2
    assert strategy.trades_iter.populated_count == 0
