Getting started
===============

This case study constructs a long-only 4/16 simple-moving-average crossover
using deterministic synthetic prices. It deliberately separates feature time,
signal time, order time, and fill time so that the backtest cannot execute on
information from the future.

Imports and synthetic data
--------------------------

Gambit uses Polars frames for labelled tabular work and NumPy arrays at the
event-driven strategy boundary::

   import numpy as np
   import polars as pl
   import gambit as gb

   close = (
       [100.0] * 16
       + list(np.arange(101.0, 113.0))
       + list(np.arange(111.0, 79.0, -1.0))
   )
   timestamps = np.arange(
       np.datetime64("2024-01-02"),
       np.datetime64("2024-01-02") + np.timedelta64(len(close), "D"),
   )
   data = pl.DataFrame({"timestamp": timestamps, "close": close})

The flat, rising, and falling sections produce exactly one bullish and one
bearish crossover. Deterministic research inputs make failures reproducible.

Compute features and signals
----------------------------

Compute both moving averages as columns. A crossover is a *transition*, not
merely the state ``ma_4 > ma_16``. Comparing the current and previous states
prevents repeated entry signals on every bar above the long average::

   data = (
       data.with_columns(
           pl.col("close").rolling_mean(window_size=4).alias("ma_4"),
           pl.col("close").rolling_mean(window_size=16).alias("ma_16"),
       )
       .with_columns(
           (
               (pl.col("ma_4") > pl.col("ma_16"))
               & (pl.col("ma_4").shift(1) <= pl.col("ma_16").shift(1))
           ).fill_null(False).alias("bullish_cross"),
           (
               (pl.col("ma_4") < pl.col("ma_16"))
               & (pl.col("ma_4").shift(1) >= pl.col("ma_16").shift(1))
           ).fill_null(False).alias("bearish_cross"),
       )
   )

The first 15 values of ``ma_16`` are null. Converting null comparisons to
``False`` explicitly documents the warm-up policy instead of relying on an
implicit missing-value convention.

Define prices and instruments
------------------------------

Every order and valuation asks a price function for the price of a contract at
an event index. ``PriceFuncArrays`` provides an efficient implementation::

   gb.Contract.clear_cache()
   gb.ContractGroup.clear_cache()

   event_times = data["timestamp"].to_numpy().astype("datetime64[D]")
   prices = data["close"].to_numpy()
   symbols = np.full(data.height, "SYNTH-MA")
   price_function = gb.PriceFuncArrays(symbols, event_times, prices)

   group = gb.ContractGroup.get("MA-CROSSOVER")
   contract = gb.Contract.create("SYNTH-MA", group)

Contract groups determine which indicators and rules run together and how P&L
is aggregated. A dedicated group also isolates the example from other
instruments in a long-running research process.

Build and run the strategy
--------------------------

The entry invests up to 100% of current equity. The exit closes the existing
long position. ``trade_lag=1`` means a signal observed on bar *t* can execute no
earlier than bar *t+1*::

   builder = gb.StrategyBuilder(data)
   builder.timestamp_unit = np.dtype("datetime64[D]")
   builder.set_starting_equity(10_000.0)
   builder.set_trade_lag(1)
   builder.set_log_trades(False)
   builder.add_contract_group(group)
   builder.set_price_function(price_function)

   for column in ("close", "ma_4", "ma_16"):
       builder.add_series_indicator(column, column)

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
   result = strategy.run()

Inspect results
---------------

Trades retain their generating order and reason code::

   trades = strategy.trades()
   summary = pl.DataFrame(
       {
           "timestamp": [trade.timestamp for trade in trades],
           "reason": [trade.order.reason_code for trade in trades],
           "quantity": [trade.qty for trade in trades],
           "price": [trade.price for trade in trades],
       }
   )
   print(summary)

The expected fills are a purchase of 98 shares at 102 on January 19 and a sale
at 105 on February 5. The position is flat and ending equity is 10,294::

   assert strategy.account.position(group, event_times[-1]) == 0
   assert strategy.account.equity(event_times[-1]) == 10_294.0

This positive result proves that the mechanics work; it is not evidence that a
moving-average strategy has predictive value. Real research must include costs,
corporate-action-adjusted data, session calendars, parameter stability, and an
out-of-sample protocol.

Next steps
----------

Read :doc:`core_concepts` for the event lifecycle, :doc:`pitfalls` before using
real capital, and :doc:`examples` for costs, validation, risk, and statistical
analysis recipes.
