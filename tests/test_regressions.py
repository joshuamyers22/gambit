from types import SimpleNamespace

import numpy as np
import pytest

from gambit import _io
from gambit.account import Account
from gambit.optimize import Experiment, Optimizer, OptimizerWorkerError
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Trade
from gambit.strategy import Strategy


def _price(_contract, _timestamps, _index, _context):
    return 100.0


def _square_cost(suggestion):
    value = suggestion["x"]
    return float(value * value), {"value": float(value)}


def _failing_cost(suggestion):
    raise ValueError(f"invalid value {suggestion['x']}")


def test_account_indexes_each_trade_under_its_own_contract():
    group = ContractGroup.get("stocks")
    first = Contract.create("FIRST", group)
    second = Contract.create("SECOND", group)
    timestamps = np.array(["2024-01-02", "2024-01-03"], dtype="datetime64[D]")
    account = Account([group], timestamps, _price, SimpleNamespace())

    trades = [
        Trade(first, MarketOrder(contract=first, timestamp=timestamps[0], qty=1), timestamps[0], 1, 100.0),
        Trade(second, MarketOrder(contract=second, timestamp=timestamps[0], qty=1), timestamps[0], 1, 100.0),
    ]
    account.add_trades(trades)

    day = timestamps[0]
    assert account._trades_for_date[("FIRST", day)] == [trades[0]]
    assert account._trades_for_date[("SECOND", day)] == [trades[1]]


def test_orders_filters_by_contract_group():
    first_group = ContractGroup.get("first")
    second_group = ContractGroup.get("second")
    first = Contract.create("FIRST", first_group)
    second = Contract.create("SECOND", second_group)
    timestamps = np.array(["2024-01-02", "2024-01-03"], dtype="datetime64[D]")
    strategy = Strategy(timestamps, [first_group, second_group], _price)
    first_order = MarketOrder(contract=first, timestamp=timestamps[0], qty=1)
    second_order = MarketOrder(contract=second, timestamp=timestamps[0], qty=1)
    strategy._orders = [first_order, second_order]

    assert strategy.orders(first_group) == [first_order]
    assert strategy.orders(second_group) == [second_order]


def test_optimizer_cost_order_names_match_sort_direction():
    optimizer = Optimizer("sort", iter(()), lambda _suggestion: (0.0, {}), max_processes=1)
    optimizer.experiments = [
        Experiment({"x": 1}, 4.0, {}),
        Experiment({"x": 2}, -2.0, {}),
        Experiment({"x": 3}, 1.0, {}),
    ]

    assert [item.cost for item in optimizer.experiment_list("lowest_cost")] == [-2.0, 1.0, 4.0]
    assert [item.cost for item in optimizer.experiment_list("highest_cost")] == [4.0, 1.0, -2.0]


def test_optimizer_runs_with_spawn_and_bounded_pending_work():
    suggestions = ({"x": value} for value in range(4))
    optimizer = Optimizer(
        "spawn", suggestions, _square_cost, max_processes=2, process_start_method="spawn", max_pending_tasks=2
    )

    optimizer.run(raise_on_error=True)

    assert sorted(experiment.cost for experiment in optimizer.experiments) == [0.0, 1.0, 4.0, 9.0]


def test_optimizer_chains_worker_error_with_suggestion_context():
    optimizer = Optimizer("failure", iter([{"x": 7}]), _failing_cost, max_processes=2)

    with pytest.raises(OptimizerWorkerError, match=r"\{'x': 7\}") as error:
        optimizer.run(raise_on_error=True)

    assert isinstance(error.value.__cause__, ValueError)


def test_df_data_applies_inclusive_date_bounds_to_all_columns():
    group = ContractGroup.get("stocks")
    Contract.create("FIRST", group)
    timestamps = np.array(["2024-01-01", "2024-01-02", "2024-01-03"], dtype="datetime64[D]")
    strategy = Strategy(timestamps, [group], _price)
    strategy.indicator_values[group.name] = SimpleNamespace(indicator=np.array([10, 20, 30]))
    strategy.signal_values[group.name] = SimpleNamespace(signal=np.array([-1, 0, 1]))

    result = strategy.df_data(
        add_pnl=False,
        start_date=np.datetime64("2024-01-02"),
        end_date=np.datetime64("2024-01-03"),
    )

    assert np.array_equal(result["timestamp"].to_numpy().astype("datetime64[D]"), timestamps[1:])
    assert result["indicator"].to_list() == [20, 30]
    assert result["signal"].to_list() == [0, 1]


def test_native_datetime_parser_owns_memory_and_uses_utc_calendar_rules():
    values = np.array(["2024-01-02T03:04:05", "2100-03-01T00:00:00"])

    parsed = _io.parse_datetimes(values)

    assert np.array_equal(parsed, values.astype("datetime64[s]"))


def test_native_datetime_parser_rejects_invalid_input():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        _io.parse_datetimes(np.array(["not-a-date"]))
