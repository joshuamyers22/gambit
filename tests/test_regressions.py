from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from gambit import _io
from gambit.account import Account
from gambit.optimize import Experiment, Optimizer, OptimizerWorkerError, flatten_keys
from gambit.pq_types import DEFAULT_CG, Contract, ContractGroup, MarketOrder, Trade
from gambit.pq_utils import find_in_subdir, shift_np
from gambit.strategy import Strategy
from gambit.strategy_components import BracketOrderEntryRule, VWAPEntryRule


def test_contract_group_cache_clear_removes_contracts_without_replacing_default() -> None:
    named_group = ContractGroup.get("named")
    Contract.create("DEFAULT-CONTRACT")
    Contract.create("NAMED-CONTRACT", named_group)

    Contract.clear_cache()
    ContractGroup.clear_cache()

    assert DEFAULT_CG.get_contracts() == []
    assert named_group.get_contracts() == []
    assert ContractGroup.get_default() is DEFAULT_CG
    assert ContractGroup.get("DEFAULT") is DEFAULT_CG
    assert not ContractGroup.exists("named")


def test_contract_cache_clear_also_removes_group_references() -> None:
    group = ContractGroup.get("sector")
    Contract.create("AAPL", group)

    Contract.clear_cache()

    assert group.get_contracts() == []


def test_trade_representation_omits_whitespace_for_absent_optional_fields() -> None:
    contract = Contract.create("IBM", ContractGroup.get("repr"))
    order = MarketOrder(contract=contract, timestamp=np.datetime64("2019-01-01T14:59"), qty=100)
    trade = Trade(contract, order, np.datetime64("2019-01-01T15:00"), 100, 10.213, fee=0.01)

    assert repr(trade) == (
        "IBM 2019-01-01 15:00:00 qty: 100 prc: 10.213 fee: 0.01 "
        "order: IBM 2019-01-01 14:59:00 qty: 100 OrderStatus.OPEN"
    )


@pytest.mark.parametrize("quantity", [0.5, -1.5, True])
def test_trade_rejects_non_whole_quantities(quantity) -> None:
    contract = Contract.create("WHOLE-UNITS", ContractGroup.get("whole-units"))
    timestamp = np.datetime64("2026-01-02")
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)

    with pytest.raises(ValueError, match="whole shares or contracts"):
        Trade(contract, order, timestamp, quantity, 100.0)


def _price(_contract, _timestamps, _index, _context):
    return 100.0


def _square_cost(suggestion):
    value = suggestion["x"]
    return float(value * value), {"value": float(value)}


def _failing_cost(suggestion):
    raise ValueError(f"invalid value {suggestion['x']}")


def test_zero_shift_returns_an_independent_unchanged_array():
    original = np.array([1.0, 2.0, 3.0])

    shifted = shift_np(original, 0)

    np.testing.assert_array_equal(shifted, original)
    assert shifted is not original


def test_find_in_subdir_honors_root_and_is_deterministic(tmp_path):
    requested_root = tmp_path / "requested"
    first = requested_root / "a" / "prices.csv"
    second = requested_root / "b" / "prices.csv"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.touch()
    second.touch()

    assert find_in_subdir(str(requested_root), "prices.csv") == str(first)
    assert find_in_subdir(str(tmp_path / "absent"), "prices.csv") == ""


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
    assert [trade.contract for trade in account._trades_for_date[("FIRST", day)]] == [first]
    assert [trade.contract for trade in account._trades_for_date[("SECOND", day)]] == [second]


def test_empty_account_pnl_queries_have_stable_results() -> None:
    group = ContractGroup.get("empty-account")
    Contract.create("NEVER-TRADED", group)
    timestamps = np.array(["2026-01-02"], dtype="datetime64[D]")
    account = Account([group], timestamps, _price, SimpleNamespace())

    detailed = account.df_pnl()
    aggregate = account.df_account_pnl(group)

    assert detailed.is_empty()
    assert detailed.schema == {
        "timestamp": pl.Datetime("ns"),
        "contract_group": pl.String,
        "symbol": pl.String,
        "position": pl.Float64,
        "price": pl.Float64,
        "unrealized": pl.Float64,
        "realized": pl.Float64,
        "commission": pl.Float64,
        "fee": pl.Float64,
        "net_pnl": pl.Float64,
    }
    assert aggregate["net_pnl"].to_list() == [0.0, 0.0]
    assert aggregate["equity"].to_list() == [account.starting_equity, account.starting_equity]


def test_account_pnl_query_rejects_unknown_group_without_registering_it() -> None:
    group = ContractGroup.get("configured-pnl-query")
    account = Account([group], np.array(["2026-01-02"], dtype="datetime64[D]"), _price, SimpleNamespace())
    unknown_name = "unknown-pnl-query"

    assert not ContractGroup.exists(unknown_name)
    with pytest.raises(ValueError, match="not configured for this account"):
        account.df_pnl([unknown_name])
    assert not ContractGroup.exists(unknown_name)


def test_aggregate_account_pnl_rejects_unconfigured_group() -> None:
    configured = ContractGroup.get("configured-aggregate-query")
    account = Account(
        [configured],
        np.array(["2026-01-02"], dtype="datetime64[D]"),
        _price,
        SimpleNamespace(),
    )
    unconfigured = ContractGroup.get("unconfigured-aggregate-query")

    with pytest.raises(ValueError, match="not configured for this account"):
        account.df_account_pnl(unconfigured)


def test_account_position_and_trade_queries_reject_unconfigured_group() -> None:
    timestamp = np.datetime64("2026-01-02")
    configured = ContractGroup.get("configured-position-query")
    unconfigured = ContractGroup.get("unconfigured-position-query")
    account = Account([configured], np.array([timestamp]), _price, SimpleNamespace())
    queries = [
        lambda: account.position(unconfigured, timestamp),
        lambda: account.positions(unconfigured, timestamp),
        lambda: account.trades(unconfigured),
        lambda: account.roundtrip_trades(unconfigured),
        lambda: account.df_trades(unconfigured),
        lambda: account.df_roundtrip_trades(unconfigured),
    ]

    for query in queries:
        with pytest.raises(ValueError, match="not configured for this account"):
            query()


def test_sparse_account_calculation_resumes_from_latest_ledger_timestamp() -> None:
    timestamps = np.array(
        [
            "2026-01-01T09:00",
            "2026-01-01T15:00",
            "2026-01-02T09:00",
            "2026-01-02T15:00",
            "2026-01-03T09:00",
            "2026-01-03T15:00",
        ],
        dtype="datetime64[m]",
    )
    account = Account([ContractGroup.get("sparse-ledger")], timestamps, _price, SimpleNamespace())
    account._pnl[timestamps[3]] = 0.0

    account.calc(timestamps[-1])

    ledger_timestamps = list(account._pnl.keys())
    assert timestamps[1] not in ledger_timestamps
    assert ledger_timestamps == [timestamps[3], timestamps[-1]]


def test_account_daily_calculation_includes_exact_configured_time() -> None:
    timestamps = np.array(
        ["2026-01-01T09:00", "2026-01-01T15:00", "2026-01-02T09:00", "2026-01-02T15:00"],
        dtype="datetime64[m]",
    )

    account = Account([ContractGroup.get("exact-calc-time")], timestamps, _price, SimpleNamespace())

    assert np.array_equal(account.calc_timestamps, timestamps[[1, 3]])


def test_account_daily_calculation_does_not_reuse_prior_days_bar() -> None:
    timestamps = np.array(
        ["2026-01-01T16:00", "2026-01-02T14:00", "2026-01-02T16:00"],
        dtype="datetime64[m]",
    )

    account = Account([ContractGroup.get("daily-bar-boundary")], timestamps, _price, SimpleNamespace())

    assert np.array_equal(account.calc_timestamps, timestamps[[1]])


def test_adding_trades_invalidates_cached_future_equity() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    group = ContractGroup.get("cached-account-equity")
    contract = Contract.create("CACHED-EQUITY", group)
    account = Account([group], timestamps, lambda *_args: 110.0, SimpleNamespace(), starting_equity=1_000.0)

    def trade() -> Trade:
        order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=1)
        return Trade(contract, order, timestamps[0], 1, 100.0)

    account.add_trades([trade()])
    assert account.equity(timestamps[-1]) == 1_010.0

    account.add_trades([trade()])

    assert account.position(group, timestamps[-1]) == 2
    assert account.equity(timestamps[-1]) == 1_020.0


def test_bracket_entry_rule_honors_single_entry_per_contract_per_day() -> None:
    timestamp = np.datetime64("2026-01-02T10:00")
    group = ContractGroup.get("single-daily-entry")
    contract = Contract.create("SINGLE-DAILY-ENTRY", group)
    timestamps = np.array([timestamp])
    account = Account([group], timestamps, _price, SimpleNamespace())
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    account.add_trades([Trade(contract, order, timestamp, 1, 100.0)])
    rule = BracketOrderEntryRule("ENTRY", _price, single_entry_per_day=True)

    orders = rule(
        group,
        0,
        timestamps,
        SimpleNamespace(),
        np.array([True]),
        account,
        [],
        SimpleNamespace(),
    )

    assert orders == []


def test_vwap_entry_rule_honors_single_entry_per_group_per_day() -> None:
    timestamp = np.datetime64("2026-01-02T10:00")
    group = ContractGroup.get("single-daily-group-entry")
    traded_contract = Contract.create("GROUP-ENTRY-FIRST", group)
    Contract.create("GROUP-ENTRY-SECOND", group)
    timestamps = np.array([timestamp])
    account = Account([group], timestamps, _price, SimpleNamespace())
    order = MarketOrder(contract=traded_contract, timestamp=timestamp, qty=1)
    account.add_trades([Trade(traded_contract, order, timestamp, 1, 100.0)])
    rule = VWAPEntryRule("ENTRY", 5, _price, single_entry_per_day=True)

    orders = rule(group, 0, timestamps, SimpleNamespace(), np.array([True]), account, [], SimpleNamespace())

    assert orders == []


def test_account_pnl_is_typed_and_empty_when_no_daily_valuation_exists() -> None:
    timestamps = np.array(["2026-01-01T16:00"], dtype="datetime64[m]")
    account = Account([ContractGroup.get("no-daily-valuation")], timestamps, _price, SimpleNamespace())

    aggregate = account.df_account_pnl()

    assert aggregate.is_empty()
    assert aggregate.schema == {
        "timestamp": pl.Datetime("ns"),
        "position": pl.Float64,
        "unrealized": pl.Float64,
        "realized": pl.Float64,
        "commission": pl.Float64,
        "fee": pl.Float64,
        "net_pnl": pl.Float64,
        "equity": pl.Float64,
    }


def test_empty_strategy_orders_have_stable_string_schema() -> None:
    timestamps = np.array(["2026-01-02"], dtype="datetime64[D]")
    strategy = Strategy(timestamps, [ContractGroup.get("empty-orders")], _price)

    orders = strategy.df_orders()

    assert orders.is_empty()
    assert orders.schema == {
        "symbol": pl.String,
        "type": pl.String,
        "timestamp": pl.Datetime("ns"),
        "qty": pl.Float64,
        "reason_code": pl.String,
        "order_props": pl.String,
        "contract_props": pl.String,
    }


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


def test_single_process_optimizer_does_not_skip_generator_suggestions():
    def suggestions():
        for value in range(5):
            yield {"x": value}

    optimizer = Optimizer("complete", suggestions(), _square_cost, max_processes=1)

    optimizer.run()

    assert [experiment.suggestion["x"] for experiment in optimizer.experiments] == list(range(5))


def test_single_process_optimizer_returns_each_result_to_adaptive_generator():
    received = []

    def suggestions():
        for value in range(3):
            feedback = yield {"x": value}
            received.append(feedback)

    optimizer = Optimizer("adaptive", suggestions(), _square_cost, max_processes=1)

    optimizer.run()

    assert [experiment.suggestion["x"] for experiment in optimizer.experiments] == [0, 1, 2]
    assert received == [
        (0.0, {"value": 0.0}),
        (1.0, {"value": 1.0}),
        (4.0, {"value": 2.0}),
    ]


def test_optimizer_empty_results_and_auxiliary_columns_are_deterministic():
    optimizer = Optimizer("empty", iter(()), lambda _suggestion: (0.0, {}), max_processes=1)

    assert optimizer.df_experiments().schema == {"cost": pl.Float64}
    assert flatten_keys(
        [Experiment({"x": 1}, 1.0, {"zeta": 2.0}), Experiment({"x": 2}, 2.0, {"alpha": 3.0})]
    ) == ["alpha", "zeta"]


def test_optimizer_rejects_zero_pending_task_limit():
    with pytest.raises(ValueError, match="max_pending_tasks must be positive"):
        Optimizer("invalid", iter(()), lambda _suggestion: (0.0, {}), max_pending_tasks=0)


def test_optimizer_dataframe_supports_sparse_auxiliary_costs():
    optimizer = Optimizer("sparse", iter(()), lambda _suggestion: (0.0, {}), max_processes=1)
    optimizer.experiments = [
        Experiment({"x": 1}, 1.0, {"zeta": 2.0}),
        Experiment({"x": 2}, 2.0, {"alpha": 3.0}),
    ]

    result = optimizer.df_experiments()

    assert result.columns == ["x", "cost", "alpha", "zeta"]
    assert result["alpha"].to_list()[1] == 3.0
    assert result["zeta"].to_list()[0] == 2.0
    assert np.isnan(result["alpha"].to_list()[0])
    assert np.isnan(result["zeta"].to_list()[1])


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


def test_optimizer_cancels_pending_work_when_interrupted(monkeypatch):
    submitted = []

    class FakeExecutor:
        def __init__(self):
            self.shutdown_calls = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def submit(self, *_args):
            future = __import__("concurrent.futures").futures.Future()
            submitted.append(future)
            return future

        def shutdown(self, **kwargs):
            self.shutdown_calls.append(kwargs)

    executor = FakeExecutor()
    monkeypatch.setattr("gambit.optimize.concurrent.futures.ProcessPoolExecutor", lambda *_args, **_kwargs: executor)
    monkeypatch.setattr(
        "gambit.optimize.concurrent.futures.wait", lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    optimizer = Optimizer("interrupt", iter([{"x": 1}, {"x": 2}]), _square_cost, max_processes=2)

    with pytest.raises(KeyboardInterrupt):
        optimizer.run()

    assert submitted and all(future.cancelled() for future in submitted)
    assert executor.shutdown_calls == [{"wait": True, "cancel_futures": True}]


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
