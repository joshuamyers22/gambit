import pathlib
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from gambit import _io
from gambit.account import Account
from gambit.holiday_calendars import Calendar
from gambit.optimize import Experiment, Optimizer, OptimizerWorkerError, flatten_keys
from gambit.pq_types import DEFAULT_CG, Contract, ContractGroup, MarketOrder, Trade
from gambit.pq_utils import (
    PQException,
    bootstrap_ci,
    day_symbol,
    find_in_subdir,
    infer_compression,
    infer_frequency,
    np_bucket,
    np_find_closest,
    np_indexof,
    np_indexof_sorted,
    np_rolling_window,
    percentile_of_score,
    shift_np,
    to_csv,
)
from gambit.pq_utils import np_round as pq_np_round
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


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (np.array([1, 2, 3]), np.array([0, 1, 2])),
        (
            np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]"),
            np.array(["NaT", "2026-01-01"], dtype="datetime64[D]"),
        ),
        (np.array(["a", "b"]), np.array(["", "a"])),
    ],
)
def test_shift_uses_a_representable_default_for_the_array_dtype(original, expected):
    np.testing.assert_array_equal(shift_np(original, 1), expected)


def test_find_closest_handles_singleton_and_rejects_empty_inputs():
    assert np_find_closest(np.array([10.0]), 500.0) == 0
    np.testing.assert_array_equal(np_find_closest(np.array([10.0]), np.array([-1.0, 500.0])), [0, 0])
    with pytest.raises(ValueError, match="empty array"):
        np_find_closest(np.array([]), 1.0)


def test_numpy_index_helpers_return_first_match_or_missing_sentinel():
    unsorted = np.array([8, 3, 8, 5])
    sorted_values = np.array([3, 5, 8])

    assert np_indexof(unsorted, 8) == 0
    assert np_indexof(unsorted, 4) == -1
    assert np_indexof_sorted(sorted_values, 5) == 1
    assert np_indexof_sorted(sorted_values, 4) == -1
    assert np_indexof_sorted(sorted_values, 9) == -1


def test_day_symbol_supports_scalars_and_arrays():
    assert day_symbol(3) == "Th"
    np.testing.assert_array_equal(day_symbol(np.array([0, 4, 6, 7])), ["M", "F", "Su", ""])


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("prices.gz", "gzip"),
        ("prices.bz2", "bz2"),
        ("prices.zip", "zip"),
        ("prices.xz", "xz"),
        ("prices.csv", None),
        ("prices", None),
    ],
)
def test_infer_compression_recognizes_supported_suffixes(filename, expected):
    assert infer_compression(filename) == expected


@pytest.mark.parametrize(
    ("include_first", "include_last", "expected"),
    [(False, False, 0.0), (False, True, 0.0), (True, False, 0.0), (True, True, 1.0)],
)
def test_trading_day_count_never_makes_an_empty_same_day_interval_negative(
    include_first, include_last, expected
):
    calendar = Calendar("NYSE")

    count = calendar.num_trading_days("2024-01-02", "2024-01-02", include_first, include_last)
    days = calendar.get_trading_days("2024-01-02", "2024-01-02", include_first, include_last)

    assert count == expected
    assert isinstance(days, np.ndarray)
    assert count == len(days)


def test_trading_day_ranges_reject_reversed_scalar_and_vector_endpoints():
    calendar = Calendar("NYSE")
    starts = np.array(["2024-01-01", "2024-01-05"], dtype="datetime64[D]")
    ends = np.array(["2024-01-02", "2024-01-04"], dtype="datetime64[D]")

    with pytest.raises(ValueError, match="start date"):
        calendar.num_trading_days("2024-01-03", "2024-01-02")
    with pytest.raises(ValueError, match="start date"):
        calendar.get_trading_days("2024-01-03", "2024-01-02")
    with pytest.raises(ValueError, match="start date"):
        calendar.num_trading_days(starts, ends)


@pytest.mark.parametrize("array_endpoint", ["start", "end", "both"])
@pytest.mark.parametrize("missing", [False, True])
def test_trading_day_counts_support_zero_dimensional_arrays(array_endpoint, missing):
    calendar = Calendar("NYSE")
    start = np.datetime64("NaT" if missing else "2024-01-02")
    end = np.datetime64("2024-01-03")
    if array_endpoint in {"start", "both"}:
        start = np.array(start)
    if array_endpoint in {"end", "both"}:
        end = np.array(end)

    result = calendar.num_trading_days(start, end)

    assert isinstance(result, np.ndarray)
    assert result.shape == ()
    if missing:
        assert np.isnan(result.item())
    else:
        assert result.item() == 1.0


def test_trading_day_counts_preserve_broadcast_inputs_and_missing_dates():
    calendar = Calendar("NYSE")
    starts = np.array([["2024-01-02"], ["2024-01-03"], ["NaT"]], dtype="datetime64[D]")
    ends = np.array(["2024-01-03", "2024-01-05", "NaT"], dtype="datetime64[D]")
    original_starts, original_ends = starts.copy(), ends.copy()
    starts.setflags(write=False)
    ends.setflags(write=False)

    excluded = calendar.num_trading_days(starts, ends, include_first=False, include_last=False)
    included = calendar.num_trading_days(starts, ends, include_first=True, include_last=True)

    np.testing.assert_array_equal(excluded, [[0, 2, np.nan], [0, 1, np.nan], [np.nan, np.nan, np.nan]])
    np.testing.assert_array_equal(included, [[2, 4, np.nan], [1, 3, np.nan], [np.nan, np.nan, np.nan]])
    np.testing.assert_array_equal(starts, original_starts)
    np.testing.assert_array_equal(ends, original_ends)


@pytest.mark.parametrize(
    ("name", "expected_dates", "next_day"),
    [
        ("24/7", ["2024-01-05", "2024-01-06", "2024-01-07", "2024-01-08"], "2024-01-06"),
        ("24/5", ["2024-01-05", "2024-01-08"], "2024-01-08"),
    ],
)
def test_calendar_without_holidays_uses_its_trading_week(monkeypatch, name, expected_dates, next_day):
    monkeypatch.setattr(Calendar, "_bus_day_calendars", {})
    calendar = Calendar(name)
    dates: np.ndarray = np.arange("2024-01-05", "2024-01-09", dtype="datetime64[D]")
    expected = np.array(expected_dates, dtype="datetime64[D]")

    np.testing.assert_array_equal(dates[calendar.is_trading_day(dates)], expected)
    np.testing.assert_array_equal(calendar.get_trading_days(dates[0], dates[-1], True, True), expected)
    assert calendar.num_trading_days(dates[0], dates[-1], True, True) == len(expected)
    assert calendar.add_trading_days(dates[0], 1) == np.datetime64(next_day)
    assert Calendar(name).bus_day_cal is calendar.bus_day_cal


def test_calendar_preserves_nonstandard_weekmask_and_holidays(monkeypatch):
    import pandas_market_calendars as mcal

    monkeypatch.setattr(Calendar, "_bus_day_calendars", {})
    holiday_rules = SimpleNamespace(
        holidays=(np.datetime64("2024-01-08"),), weekmask="Sun Mon Tue Wed Thu"
    )
    monkeypatch.setattr(mcal, "get_calendar", lambda _name: SimpleNamespace(holidays=lambda: holiday_rules))
    calendar = Calendar("test-sunday-through-thursday")
    dates: np.ndarray = np.arange("2024-01-04", "2024-01-10", dtype="datetime64[D]")
    expected = np.array(["2024-01-04", "2024-01-07", "2024-01-09"], dtype="datetime64[D]")

    np.testing.assert_array_equal(dates[calendar.is_trading_day(dates)], expected)
    np.testing.assert_array_equal(calendar.get_trading_days(dates[0], dates[-1], True, True), expected)
    assert calendar.num_trading_days(dates[0], dates[-1], True, True) == 3
    assert calendar.add_trading_days(dates[0], 1) == np.datetime64("2024-01-07")


@pytest.mark.parametrize(
    "values",
    [
        pl.Series([1, 2]),
        pl.Series([1.0, 2.0]),
        pl.Series([True, False]),
        pl.Series(["2024-01-05", "2024-01-08"]),
        pl.Series([1, 2], dtype=pl.Duration("us")),
        pl.Series([None, None]),
    ],
)
def test_calendar_rejects_polars_columns_without_a_date_dtype(values):
    calendar = Calendar("NYSE")
    with pytest.raises(TypeError, match="Date or Datetime"):
        calendar.is_trading_day(values)
    with pytest.raises(TypeError, match="Date or Datetime"):
        calendar.num_trading_days(values, "2024-01-08")
    with pytest.raises(TypeError, match="Date or Datetime"):
        calendar.get_trading_days("2024-01-05", values)
    with pytest.raises(TypeError, match="Date or Datetime"):
        calendar.add_trading_days(values, 1)


@pytest.mark.parametrize("dtype", [pl.Date, pl.Datetime("ms"), pl.Datetime("us"), pl.Datetime("ns")])
def test_calendar_polars_dates_preserve_missing_values_and_offset_precision(dtype):
    calendar = Calendar("NYSE")
    values = pl.Series(
        np.array(["2024-01-05T15:30:12.123456789", "2024-01-08T09:45:01.987654321", "NaT"], dtype="M8[ns]")
    ).cast(dtype)
    original = values.clone()
    expected = pl.Series(
        np.array(["2024-01-08T15:30:12.123456789", "2024-01-09T09:45:01.987654321", "NaT"], dtype="M8[ns]")
    ).cast(dtype).to_numpy()

    np.testing.assert_array_equal(calendar.is_trading_day(values), [True, True, False])
    np.testing.assert_array_equal(calendar.num_trading_days(values, "2024-01-09"), [2.0, 1.0, np.nan])
    shifted = calendar.add_trading_days(values, 1, roll="nat")
    np.testing.assert_array_equal(shifted, expected)
    assert shifted.dtype == expected.dtype
    assert values.equals(original)


@pytest.mark.parametrize("values", [np.array([1, 2]), np.array([True, False])])
def test_calendar_membership_rejects_numpy_non_date_arrays(values):
    with pytest.raises(ValueError, match="supported date"):
        Calendar("NYSE").is_trading_day(values)


@pytest.mark.parametrize(
    "offset",
    [True, np.bool_(False), 1.5, 1.0, "1", np.array(1.5), np.array([1.5]), np.array([True, False]), np.nan],
)
def test_trading_day_offsets_reject_non_integer_counts_for_every_roll_mode(offset):
    calendar = Calendar("NYSE")
    for roll in (
        "raise", "nat", "forward", "following", "backward", "preceding",
        "modifiedfollowing", "modifiedpreceding", "allow",
    ):
        with pytest.raises(TypeError, match="num_days must be an integer"):
            calendar.add_trading_days("2024-01-05", offset, roll=roll)


@pytest.mark.parametrize("offset", [1, np.int64(1), np.array(1, dtype=np.int32)])
def test_trading_day_offsets_accept_scalar_integer_representations(offset):
    result = Calendar("NYSE").add_trading_days("2024-01-05T15:30", offset)
    assert result == np.datetime64("2024-01-08T15:30")


def test_allow_offsets_broadcast_closed_days_without_mutating_inputs():
    calendar = Calendar("NYSE")
    starts = np.array([["2024-01-05T15:30"], ["2024-01-06T15:30"]], dtype="M8[m]")
    offsets = np.array([-1, 0, 1, 2], dtype=np.int16)
    original_starts, original_offsets = starts.copy(), offsets.copy()
    starts.setflags(write=False)
    offsets.setflags(write=False)

    result = calendar.add_trading_days(starts, offsets, roll="allow")

    expected = np.array(
        [
            ["2024-01-04T15:30", "2024-01-05T15:30", "2024-01-08T15:30", "2024-01-09T15:30"],
            ["2024-01-05T15:30", "2024-01-08T15:30", "2024-01-08T15:30", "2024-01-09T15:30"],
        ],
        dtype="M8[m]",
    )
    np.testing.assert_array_equal(result, expected)
    np.testing.assert_array_equal(starts, original_starts)
    np.testing.assert_array_equal(offsets, original_offsets)


@pytest.mark.parametrize("name", ["24/7", "NYSE"])
@pytest.mark.parametrize("offset", [np.iinfo(np.int64).min, np.iinfo(np.int64).max])
@pytest.mark.parametrize("precision", ["D", "m", "ns"])
def test_trading_day_offsets_reject_day_arithmetic_overflow(name, offset, precision):
    calendar = Calendar(name)
    start = np.datetime64("1960-01-05" if offset < 0 else "2024-01-05", precision)
    for roll in ("raise", "nat", "forward", "following", "backward", "preceding",
                 "modifiedfollowing", "modifiedpreceding", "allow"):
        with pytest.raises(OverflowError, match="representable date range"):
            calendar.add_trading_days(start, offset, roll=roll)


@pytest.mark.parametrize(
    ("start", "offset"),
    [("2262-04-11T12:00:00.000000000", 1), ("1677-09-22T12:00:00.000000000", -2)],
)
def test_trading_day_offsets_reject_timestamp_overflow(start, offset):
    with pytest.raises(OverflowError, match="representable timestamp range"):
        Calendar("24/7").add_trading_days(start, offset)


@pytest.mark.parametrize("offset", [-(1 << 60), 1 << 60])
def test_trading_day_offsets_keep_large_representable_day_counts(offset):
    start = np.datetime64("2024-01-05")
    result = Calendar("24/7").add_trading_days(start, offset)
    assert int(result.view("i8")) == int(start.view("i8")) + offset


def test_nat_roll_keeps_closed_and_missing_dates_missing_with_extreme_offsets():
    starts = np.array(["2024-01-06", "NaT"], dtype="M8[ns]")
    result = Calendar("NYSE").add_trading_days(starts, np.iinfo(np.int64).max, roll="nat")
    assert np.isnat(result).all()


@pytest.mark.parametrize("boundary", [-np.iinfo(np.int64).max, np.iinfo(np.int64).max])
def test_trading_day_offsets_preserve_exact_timestamp_boundaries(boundary):
    calendar = Calendar("24/7")
    start = np.datetime64(int(boundary), "ns")
    inward = 1 if boundary < 0 else -1
    assert calendar.add_trading_days(start, 0) == start
    shifted = calendar.add_trading_days(start, inward)
    assert shifted == start + np.timedelta64(inward, "D")
    assert calendar.add_trading_days(shifted, -inward) == start
    with pytest.raises(OverflowError, match="representable timestamp range"):
        calendar.add_trading_days(start, -inward)


def test_trading_day_offset_overflow_checks_preserve_broadcast_missing_and_empty_inputs():
    calendar = Calendar("24/7")
    starts = np.array([["2262-04-10T12:00:00.000000000"], ["NaT"]], dtype="M8[ns]")
    starts.setflags(write=False)
    expected = np.array([["2262-04-10T12:00:00.000000000", "2262-04-11T12:00:00.000000000"],
                         ["NaT", "NaT"]], dtype="M8[ns]")
    np.testing.assert_array_equal(calendar.add_trading_days(starts, np.array([0, 1]), roll="nat"), expected)
    with pytest.raises(OverflowError, match="representable timestamp range"):
        calendar.add_trading_days(starts, np.array([0, 2]), roll="nat")
    result = calendar.add_trading_days(np.array([], dtype="M8[ns]"), 1)
    assert result.shape == (0,)
    assert result.dtype == np.dtype("M8[ns]")


@pytest.mark.parametrize("precision", ["3h", "2D", "W", "M", "Y"])
def test_trading_day_offsets_keep_coarse_and_scaled_timestamp_units(precision):
    start = np.datetime64("2024-01-01", precision)
    expected = start + np.timedelta64(1, "D")
    result = Calendar("24/7").add_trading_days(start, 1)
    assert result == expected
    assert result.dtype == expected.dtype


def test_trading_day_offset_validation_matches_nonstandard_calendar_across_epoch(monkeypatch):
    rules = np.busdaycalendar(
        weekmask="Sun Mon Tue Wed Thu",
        holidays=np.array(["1969-12-25", "1970-01-01"], dtype="M8[D]"),
    )
    monkeypatch.setitem(Calendar._bus_day_calendars, "offset-test", rules)
    calendar = Calendar("offset-test")
    starts: np.ndarray = np.arange("1969-12-20", "1970-01-10", dtype="M8[D]")[:, None]
    offsets = np.arange(-20, 21)
    expected = np.busday_offset(starts, offsets, roll="forward", busdaycal=rules)
    np.testing.assert_array_equal(calendar.add_trading_days(starts, offsets, roll="forward"), expected)


def test_percentile_of_score_handles_singletons_and_ties_deterministically():
    np.testing.assert_array_equal(percentile_of_score(np.array([42.0])), np.array([0.0]))
    np.testing.assert_allclose(
        percentile_of_score(np.array([3.0, 1.0, 2.0, 2.0])),
        np.array([100.0, 0.0, 50.0, 50.0]),
    )


@pytest.mark.parametrize("values", [np.array([[1.0, 2.0]]), np.array([1.0, np.nan]), np.array([np.inf])])
def test_percentile_of_score_rejects_undefined_rankings(values):
    with pytest.raises(ValueError, match="percentile input"):
        percentile_of_score(values)


def test_bootstrap_ci_can_be_reproduced_with_a_seeded_generator():
    values = np.array([1.0, 2.0, 3.0, 8.0])

    first = bootstrap_ci(values, n=100, rng=np.random.default_rng(17))
    second = bootstrap_ci(values, n=100, rng=np.random.default_rng(17))

    assert first == second


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"ci_level": 0}, "ci_level"),
        ({"ci_level": 1}, "ci_level"),
        ({"n": 0}, "iteration count"),
        ({"n": True}, "iteration count"),
    ],
)
def test_bootstrap_ci_rejects_invalid_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        bootstrap_ci(np.array([1.0, 2.0]), **kwargs)


@pytest.mark.parametrize("values", [np.array([]), np.array([[1.0, 2.0]])])
def test_bootstrap_ci_rejects_invalid_input_shape(values):
    with pytest.raises(ValueError, match="non-empty one-dimensional"):
        bootstrap_ci(values)


@pytest.mark.parametrize("window", [0, -1, 4])
def test_rolling_window_rejects_out_of_range_sizes(window: int):
    with pytest.raises(ValueError, match="between 1 and 3"):
        np_rolling_window(np.array([1, 2, 3]), window)


def test_round_rejects_invalid_increments_instead_of_returning_nan():
    for increment in (0.0, -0.25, np.nan, np.inf):
        with pytest.raises(ValueError, match="finite and positive"):
            pq_np_round(np.array([1.0]), increment)


@pytest.mark.parametrize("buckets", [[], [4, 2, 8], [2, 2, 4]])
def test_bucket_rejects_empty_or_non_increasing_boundaries(buckets):
    with pytest.raises(ValueError, match="buckets"):
        np_bucket(np.array([1, 2, 3]), buckets)


@pytest.mark.parametrize(
    "timestamps",
    [np.array([], dtype="datetime64[D]"), np.array(["2026-01-01"], dtype="datetime64[D]")],
)
def test_frequency_inference_requires_an_observable_interval(timestamps):
    with pytest.raises(PQException, match="fewer than two timestamps"):
        infer_frequency(timestamps)


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


def test_to_csv_uses_unique_atomic_staging(tmp_path):
    target = tmp_path / "prices.csv"
    legacy_temporary = tmp_path / "prices.csv.tmp"
    legacy_temporary.write_text("unrelated")

    to_csv(pl.DataFrame({"price": [1.5, 2.5]}), str(target))

    assert target.read_text() == "price\n1.5\n2.5\n"
    assert legacy_temporary.read_text() == "unrelated"
    assert list(tmp_path.glob(".prices.csv.*.tmp")) == []


def test_to_csv_cleans_up_failed_staging_file(tmp_path):
    class FailingFrame:
        def to_csv(self, path, **_kwargs):
            pathlib.Path(path).write_text("partial")
            raise RuntimeError("write failed")

    target = tmp_path / "failed.csv"
    with pytest.raises(RuntimeError, match="write failed"):
        to_csv(FailingFrame(), str(target))

    assert not target.exists()
    assert list(tmp_path.glob(".failed.csv.*.tmp")) == []


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


def test_optimizer_plots_handle_invalid_filtered_and_sparse_results():
    invalid = Optimizer("invalid", iter(()), lambda _suggestion: (0.0, {}), max_processes=1)
    invalid.experiments = [Experiment({"x": np.nan, "y": 1.0}, 1.0, {})]
    assert invalid.plot_2d("x", show=False) is None

    sparse = Optimizer("sparse", iter(()), lambda _suggestion: (0.0, {}), max_processes=1)
    sparse.experiments = [
        Experiment({"x": 1.0, "y": 1.0}, 1.0, {"alpha": 2.0}),
        Experiment({"x": 2.0, "y": 2.0}, 2.0, {"beta": 3.0}),
    ]
    figure = sparse.plot_2d("x", show=False)
    assert [trace.name for trace in figure.data] == ["cost", "alpha", "beta"]

    filtered = sparse.plot_3d("x", "y", xlim=(10.0, 20.0), show=False)
    assert len(filtered.data) == 0


def test_optimizer_plotting_does_not_write_debug_values_to_stdout(capsys):
    optimizer = Optimizer("quiet", iter(()), lambda _suggestion: (0.0, {}), max_processes=1)
    optimizer.experiments = [
        Experiment({"x": 1.0, "y": 1.0}, -1.0, {}),
        Experiment({"x": 2.0, "y": 2.0}, 1.0, {}),
    ]

    optimizer.plot_3d("x", "y", show=False)

    assert capsys.readouterr().out == ""


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
