Examples and recipes
====================

The repository's executable notebooks live in ``examples/notebooks``. The
recipes below are intentionally small enough to adapt in a script or notebook.

Deterministic random research data
----------------------------------

Seed a local generator rather than NumPy's process-wide random state::

   rng = np.random.default_rng(20260829)
   returns = rng.normal(loc=0.0002, scale=0.01, size=1_000)
   prices = 100.0 * np.exp(np.cumsum(returns))
   frame = pl.DataFrame(
       {
           "timestamp": np.arange(
               np.datetime64("2020-01-01"),
               np.datetime64("2020-01-01") + np.timedelta64(prices.size, "D"),
           ),
           "close": prices,
       }
   )

Record the seed with the experiment. Synthetic data tests mechanics and
invariants; it does not validate an investment thesis.

Statsmodels diagnostics from Polars
-----------------------------------

Statsmodels consumes NumPy arrays cleanly, so pandas conversion is unnecessary::

   import statsmodels.api as sm

   sample = frame.with_columns(
       pl.col("close").pct_change().alias("return"),
       pl.col("close").pct_change().shift(1).alias("lagged_return"),
   ).drop_nulls()

   y = sample["return"].to_numpy()
   x = sm.add_constant(sample["lagged_return"].to_numpy())
   model = sm.OLS(y, x, missing="raise").fit(cov_type="HAC", cov_kwds={"maxlags": 5})
   print(model.summary())

HAC standard errors address a specified amount of serial dependence; they do
not fix selection bias, nonstationarity, or multiple testing.

Calendar-aware validation
-------------------------

Use exchange calendars at the ingestion boundary::

   report = gambit.validate_market_data(
       bars,
       price_columns=("open", "high", "low", "close"),
       volume_columns=("volume",),
       calendar_name="NYSE",
   )
   for finding in report.findings:
       print(finding.severity, finding.code, finding.message)
   report.raise_if_invalid()

Cost sensitivity
----------------

Treat cost assumptions as parameters and report a surface, not one preferred
number::

   spreads = [0.00, 0.01, 0.02, 0.05]
   outcomes = []
   for spread in spreads:
       simulator = gambit.SimpleMarketSimulator(
           price_function,
           slippage_model=gambit.BidAskSpreadSlippage(spread),
           commission_model=gambit.PerUnitCharge(0.005),
       )
       strategy = build_strategy(simulator=simulator)
       strategy.run()
       outcomes.append(
           {"spread": spread, "ending_equity": strategy.account.equity(strategy.timestamps[-1])}
       )
   sensitivity = pl.DataFrame(outcomes)

Persist and verify a result
---------------------------

Result bundles preserve immutable tables and verify their manifest on load::

   result = strategy.run()
   result.save("research/example.gambit")
   restored = gambit.BacktestResult.load("research/example.gambit")
   assert restored.provenance.fingerprint == result.provenance.fingerprint

Factor-cache calibration
------------------------

Calibrate on the same filesystem intended for research::

   calibration = gambit.calibrate_factor_cache("/nvme/gambit-cache")
   print(calibration)

Calibration is a local measurement, not a permanent machine constant. Repeat it
after material hardware, filesystem, kernel, or workload changes.

Notebook index
--------------

``getting_started.ipynb``
   Basic builder workflow and evaluation.

``multiple_contracts.ipynb``
   Contract groups and multi-instrument strategies.

``reporting.ipynb``
   Return and trade reporting.

``options_trading.ipynb``
   Option instruments and pricing helpers.

``optimizing_strategies.ipynb``
   Parameter experiments using Polars and Statsmodels.
