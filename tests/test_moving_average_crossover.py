from __future__ import annotations

import numpy as np
import polars as pl

import gambit as gb


def _synthetic_prices() -> pl.DataFrame:
    """Return a deterministic path with one bullish and one bearish crossover."""
    close = [100.0] * 16 + list(np.arange(101.0, 113.0)) + list(np.arange(111.0, 79.0, -1.0))
    timestamps = np.arange(
        np.datetime64("2024-01-02"),
        np.datetime64("2024-01-02") + np.timedelta64(len(close), "D"),
    )
    return (
        pl.DataFrame({"timestamp": timestamps, "close": close})
        .with_columns(
            pl.col("close").rolling_mean(window_size=4).alias("ma_4"),
            pl.col("close").rolling_mean(window_size=16).alias("ma_16"),
        )
        .with_columns(
            (
                (pl.col("ma_4") > pl.col("ma_16"))
                & (pl.col("ma_4").shift(1) <= pl.col("ma_16").shift(1))
            )
            .fill_null(False)
            .alias("bullish_cross"),
            (
                (pl.col("ma_4") < pl.col("ma_16"))
                & (pl.col("ma_4").shift(1) >= pl.col("ma_16").shift(1))
            )
            .fill_null(False)
            .alias("bearish_cross"),
        )
    )


def test_four_sixteen_moving_average_crossover_with_synthetic_data() -> None:
    gb.Contract.clear_cache()
    gb.ContractGroup.clear_cache()
    data = _synthetic_prices()
    timestamps = data["timestamp"].to_numpy().astype("datetime64[D]")
    prices = data["close"].to_numpy()
    symbols = np.full(data.height, "SYNTH-MA")
    price_function = gb.PriceFuncArrays(symbols, timestamps, prices)

    contract_group = gb.ContractGroup.get("MA-CROSSOVER")
    contract = gb.Contract.create("SYNTH-MA", contract_group)
    builder = gb.StrategyBuilder(data)
    builder.timestamp_unit = np.dtype("datetime64[D]")
    builder.set_starting_equity(10_000.0)
    builder.set_trade_lag(1)
    builder.set_log_trades(False)
    builder.add_contract_group(contract_group)
    builder.set_price_function(price_function)
    builder.add_series_indicator("close", "close")
    builder.add_series_indicator("ma_4", "ma_4")
    builder.add_series_indicator("ma_16", "ma_16")
    builder.add_series_rule(
        "bullish_cross",
        gb.PercentOfEquityTradingRule(
            reason_code="BULLISH_CROSS",
            price_func=price_function,
            equity_percent=1.0,
        ),
        position_filter="zero",
    )
    builder.add_series_rule(
        "bearish_cross",
        gb.ClosePositionExitRule("BEARISH_CROSS", price_function),
        position_filter="positive",
    )

    strategy = builder()
    strategy.run()

    trades = strategy.trades()
    signal_rows = data.filter(pl.col("bullish_cross") | pl.col("bearish_cross"))
    expected_execution_times = signal_rows["timestamp"].to_numpy() + np.timedelta64(1, "D")

    assert signal_rows["bullish_cross"].to_list() == [True, False]
    assert [trade.order.reason_code for trade in trades] == ["BULLISH_CROSS", "BEARISH_CROSS"]
    assert np.array_equal(np.array([trade.timestamp for trade in trades]), expected_execution_times)
    assert trades[0].qty > 0
    assert trades[1].qty == -trades[0].qty
    assert trades[1].price > trades[0].price
    assert strategy.account.position(contract.contract_group, timestamps[-1]) == 0
    assert strategy.account.equity(timestamps[-1]) > 10_000.0
