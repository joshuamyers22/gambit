import numpy as np

from gambit.evaluator import (
    compute_dates_3yr,
    compute_gmean,
    compute_periods_per_year,
    compute_return_metrics,
    compute_returns_3yr,
    compute_rolling_dd_3yr,
    compute_sharpe,
    compute_sortino,
    handle_non_finite_returns,
)


def test_public_scalar_metrics_return_python_floats() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02", "2026-01-03"], dtype="datetime64[D]")
    returns = np.array([0.001, -0.001, 0.002])

    assert type(compute_periods_per_year(timestamps)) is float
    assert type(compute_gmean(timestamps, returns, 252.0)) is float
    assert type(compute_sharpe(returns, 0.001, 252.0)) is float
    assert type(compute_sortino(returns, 0.001, 252.0)) is float


def test_calendar_periodic_returns_use_calendar_annualization() -> None:
    monthly = np.array(["2025-01-31", "2025-02-28", "2025-03-31", "2025-04-30"], dtype="datetime64[D]")
    every_two_months = np.array(
        ["2025-01-31", "2025-03-31", "2025-05-31", "2025-07-31"], dtype="datetime64[D]"
    )

    assert compute_periods_per_year(monthly) == 12.0
    assert compute_periods_per_year(every_two_months) == 6.0


def test_geometric_mean_is_missing_when_history_spans_zero_periods() -> None:
    timestamps = np.array(["2026-01-02"], dtype="datetime64[D]")

    result = compute_gmean(timestamps, np.array([0.01]), 252.0)

    assert np.isnan(result)


def test_short_history_metrics_do_not_divide_by_zero_in_annual_bucket() -> None:
    timestamps = np.array(["2025-12-31", "2026-01-01"], dtype="datetime64[D]")

    metrics = compute_return_metrics(timestamps, np.array([0.0, 0.0]), 1_000.0).metrics()

    assert metrics["gmean"] == 0.0
    assert np.isnan(metrics["annual_returns"][1]).all()


def test_non_finite_return_normalization_uses_zero_not_float_extremes() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"], dtype="datetime64[D]")
    returns = np.array([np.inf, 0.01, -np.inf, np.nan])

    trimmed_timestamps, trimmed = handle_non_finite_returns(timestamps, returns.copy(), False, True)
    zeroed_timestamps, zeroed = handle_non_finite_returns(timestamps, returns.copy(), True, True)

    np.testing.assert_array_equal(trimmed_timestamps, timestamps[1:])
    np.testing.assert_array_equal(trimmed, [0.01, 0.0, 0.0])
    np.testing.assert_array_equal(zeroed_timestamps, timestamps)
    np.testing.assert_array_equal(zeroed, [0.0, 0.01, 0.0, 0.0])


def test_three_year_window_is_leap_day_safe_and_includes_cutoff() -> None:
    timestamps = np.array(["2021-02-27", "2021-02-28", "2024-02-29"], dtype="datetime64[D]")
    returns = np.array([0.01, 0.02, 0.03])
    equity = np.array([100.0, 102.0, 105.0])

    np.testing.assert_array_equal(compute_dates_3yr(timestamps), timestamps[1:])
    np.testing.assert_array_equal(compute_returns_3yr(timestamps, returns), returns[1:])
    rolling_timestamps, _ = compute_rolling_dd_3yr(timestamps, equity)
    np.testing.assert_array_equal(rolling_timestamps, timestamps[1:])
