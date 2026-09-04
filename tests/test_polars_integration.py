from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from gambit.pq_utils import resample_trade_bars, resample_ts
from gambit.strategy_builder import StrategyBuilder


def test_strategy_builder_accepts_polars_boolean_signals() -> None:
    data = pl.DataFrame(
        {
            "timestamp": np.array(["2024-01-01", "2024-01-02"], dtype="datetime64[ns]"),
            "signal": [True, False],
        }
    )
    builder = StrategyBuilder(data)

    builder.add_series_rule("signal", lambda *args: [], position_filter="zero")

    assert builder.signals[0][0] == "signal_sig"


def test_resample_trade_bars_preserves_empty_intervals_and_vwap() -> None:
    bars = pl.DataFrame(
        {
            "timestamp": np.array(["2024-01-01T09:30", "2024-01-01T09:35", "2024-01-01T09:45"], dtype="datetime64[ns]"),
            "o": [10.0, 11.0, 13.0],
            "h": [11.0, 12.0, 14.0],
            "l": [9.0, 10.0, 12.0],
            "c": [10.5, 11.5, 13.5],
            "v": [100.0, 300.0, 200.0],
            "vwap": [10.0, 11.0, 13.0],
        }
    )

    result = resample_trade_bars(bars, "5T")

    assert result.height == 4
    assert result["timestamp"].dtype == pl.Datetime("ns")
    assert result["vwap"][1] == 11.0
    assert result["vwap"][2] is None


def test_resample_ts_returns_numpy_with_null_gap() -> None:
    dates = np.array(["2024-01-01T09:30", "2024-01-01T09:40"], dtype="datetime64[ns]")
    result_dates, result_values = resample_ts(dates, np.array([1.0, 3.0]), "5T")

    assert result_dates.dtype == np.dtype("datetime64[ns]")
    assert result_dates.shape == result_values.shape == (3,)
    assert np.isnan(result_values[1])


def test_resample_trade_bars_executes_custom_aggregations() -> None:
    bars = pl.DataFrame(
        {
            "timestamp": np.array(["2024-01-01T09:30", "2024-01-01T09:31"], dtype="datetime64[ns]"),
            "signal": [1.0, 3.0],
            "vwap": [10.0, 12.0],
            "v": [1.0, 1.0],
        }
    )

    result = resample_trade_bars(
        bars,
        "5m",
        {"signal": lambda column: column.mean(), "vwap": lambda column: column.max()},
    )

    assert result["signal"].to_list() == [2.0]
    assert result["vwap"].to_list() == [12.0]


def test_resampling_rejects_invalid_schema_and_mismatched_arrays() -> None:
    with pytest.raises(ValueError, match="timestamp.*date"):
        resample_trade_bars(pl.DataFrame({"c": [1.0]}), "1d")
    with pytest.raises(ValueError, match="not found"):
        resample_trade_bars(
            pl.DataFrame({"timestamp": np.array(["2024-01-01"], dtype="datetime64[ns]")}),
            "1d",
            {"missing": lambda column: column.last()},
        )
    with pytest.raises(ValueError, match="equal lengths"):
        resample_ts(
            np.array(["2024-01-01", "2024-01-02"], dtype="datetime64[D]"),
            np.array([1.0]),
            "1d",
        )
