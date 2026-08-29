Pitfalls and adversarial checks
===============================

Look-ahead bias
---------------

The timestamp attached to a bar does not say when every field became known. A
daily close cannot generally generate a fill at that same close. Use an
execution lag or a later price, and test the expected signal-to-fill timestamps.

Warm-up and missing values
--------------------------

Rolling features have a warm-up region. Preserve nulls until the signal policy
handles them explicitly. Filling a moving average backward imports future data;
converting every null numeric value to zero creates artificial crossings.

Final positions
---------------

An open position at the end of a test has unrealized P&L, not a completed trade.
Decide whether the research question calls for mark-to-market reporting or an
explicit liquidation rule. Do not add a favorable fictional exit solely to make
trade statistics easier to read.

Execution realism
-----------------

Market orders require a side-aware fill model. Include spread, impact, latency,
fees, participation limits, partial fills, and rejected orders at a fidelity
appropriate to the horizon. Tick speed cannot compensate for optimistic fills.

Parameter selection
-------------------

A 4/16 crossover is one hypothesis among many correlated choices. Separate
training, validation, and final holdout periods. Record every attempted variant,
not only the winner, and use stability regions rather than a single sharp optimum.

Calendar and timezone errors
----------------------------

Normalize timezone-aware source timestamps before converting to NumPy. Exchange
calendars describe sessions and special closes; they do not repair incorrectly
localized timestamps. Test daylight-saving transitions and half days.

Global instrument registries
----------------------------

Contracts and groups are cached by identity. Clear registries between independent
tests and use dedicated groups. Gambit's cache clear preserves the shared default
group object while removing its contracts, so imported references do not retain
stale instruments.

Cache benchmarking
------------------

Warm page-cache results are not NVMe results. State whether data was resident,
measure physical device deltas where supported, and compare the cached pipeline
against recomputation end to end. A faster isolated read may not improve a
backtest dominated by Python callbacks or accounting.

Statistical interpretation
--------------------------

Sharpe ratios are estimates with sampling error and dependence. Inspect the
return series, turnover, exposure, drawdown path, autocorrelation, changing
volatility, and sensitivity to costs. Statsmodels can support diagnostics, but
no summary statistic validates the economic hypothesis by itself.
