import numpy as np
import pytest

from gambit.pq_types import Contract, ContractGroup, MarketOrder, RollOrder, TimeInForce
from gambit.risk import MaxOrderQuantity
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator


def setup_contracts():
    group = ContractGroup.get("roll-identity")
    old = Contract.create("ROLL-OLD", group)
    new = Contract.create("ROLL-NEW", group)
    timestamps = np.array(["2026-09-10T10:00", "2026-09-10T10:01", "2026-09-10T10:02"], dtype="datetime64[ns]")
    return group, old, new, timestamps


def roll(old, new, timestamp, qty=2):
    return RollOrder(contract=old, reopen_contract=new, timestamp=timestamp,
                     close_qty=-qty, reopen_qty=qty, time_in_force=TimeInForce.GTC)


def ids(strategy):
    return [order.properties._gambit_roll_id for order in strategy.orders()
            if hasattr(order.properties, "_gambit_roll_id")]


@pytest.mark.parametrize("execute", [False, True])
def test_replay_order_and_trade_records_do_not_depend_on_roll_addresses(execute):
    group, old, new, timestamps = setup_contracts()
    # Keep both source objects alive to ensure their addresses are distinct.
    commands = [roll(old, new, timestamps[0]), roll(old, new, timestamps[0])]
    results = []
    strategies = []
    for command in commands:
        strategy = Strategy(timestamps, [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
        strategy.add_signal("entry", lambda *_: np.array([True, False, False]))
        strategy.add_rule("entry", lambda *_, command=command: [command], "entry")
        if execute:
            strategy.add_market_sim(SimpleMarketSimulator(lambda *_: 100.0))
        results.append(strategy.run())
        strategies.append(strategy)
    assert ids(strategies[0]) == ids(strategies[1]) == ["strategy-roll:0:0"] * 2
    assert results[0].orders.equals(results[1].orders)
    assert results[0].trades.equals(results[1].trades)
    assert vars(commands[0].properties) == vars(commands[1].properties) == {}


@pytest.mark.parametrize("execute", [False, True])
def test_equal_rolls_in_same_batch_have_distinct_stable_pairs(execute):
    group, old, new, timestamps = setup_contracts()
    commands = [roll(old, new, timestamps[0]), roll(old, new, timestamps[0])]
    strategy = Strategy(timestamps, [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    strategy.add_signal("entry", lambda *_: np.array([True, False, False]))
    strategy.add_rule("entry", lambda *_: commands, "entry")
    if execute:
        strategy.add_market_sim(SimpleMarketSimulator(lambda *_: 100.0))
    strategy.run()
    assert ids(strategy) == ["strategy-roll:0:0"] * 2 + ["strategy-roll:0:1"] * 2
    assert strategy.account.trade_count == (4 if execute else 0)


@pytest.mark.parametrize("reject", [False, True])
def test_reusing_roll_command_in_later_rule_gets_a_new_submission_pair(reject):
    group, old, new, timestamps = setup_contracts()
    command = roll(old, new, timestamps[0])
    strategy = Strategy(timestamps, [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    strategy.add_signal("entry", lambda *_: np.array([True, False, False]))
    strategy.add_rule("first", lambda *_: [command], "entry")
    strategy.add_rule("second", lambda *_: [command], "entry")
    if reject:
        strategy.add_risk_policy(MaxOrderQuantity(1))
    strategy.run()
    assert ids(strategy) == ["strategy-roll:0:0"] * 2 + ["strategy-roll:2:0"] * 2
    assert len(strategy.order_decisions) == 4


def test_multiple_heartbeats_and_ordinary_orders_have_unique_roll_ids():
    group, old, new, timestamps = setup_contracts()
    strategy = Strategy(timestamps, [group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    strategy.add_signal("entry", lambda *_: np.ones(3, dtype=bool))

    def rule(_group, index, *_args):
        return [MarketOrder(contract=old, timestamp=timestamps[index], qty=1),
                roll(old, new, timestamps[index])]

    strategy.add_rule("entry", rule, "entry")
    strategy.run()
    assert ids(strategy) == ["strategy-roll:0:1"] * 2 + ["strategy-roll:3:1"] * 2 + ["strategy-roll:6:1"] * 2


def test_distinct_roll_pairs_stay_atomic_when_one_incoming_contract_has_no_price():
    group, old, new, timestamps = setup_contracts()
    missing = Contract.create("ROLL-MISSING", group)
    commands = [roll(old, new, timestamps[0]), roll(old, missing, timestamps[0])]

    def price(contract, *_args):
        return np.nan if contract is missing else 100.0

    strategy = Strategy(timestamps, [group], price, trade_lag=0, log_trades=False)
    strategy.add_signal("entry", lambda *_: np.array([True, False, False]))
    strategy.add_rule("entry", lambda *_: commands, "entry")
    strategy.add_market_sim(SimpleMarketSimulator(price))
    strategy.run()
    assert [trade.contract.symbol for trade in strategy.trades()] == [old.symbol, new.symbol]
    assert [trade.order.properties._gambit_roll_id for trade in strategy.trades()] == ["strategy-roll:0:0"] * 2
    assert len(strategy._current_orders) == 2


def test_roll_ids_are_unique_across_contract_groups():
    group, old, new, timestamps = setup_contracts()
    second_group = ContractGroup.get("second-roll-group")
    second_old = Contract.create("SECOND-OLD", second_group)
    second_new = Contract.create("SECOND-NEW", second_group)
    commands = {group.name: roll(old, new, timestamps[0]),
                second_group.name: roll(second_old, second_new, timestamps[0])}
    strategy = Strategy(timestamps, [group, second_group], lambda *_: 100.0, trade_lag=0, log_trades=False)
    strategy.add_signal("entry", lambda *_: np.array([True, False, False]))
    strategy.add_rule("entry", lambda current_group, *_: [commands[current_group.name]], "entry")
    strategy.run()
    assert ids(strategy) == ["strategy-roll:0:0"] * 2 + ["strategy-roll:2:0"] * 2
