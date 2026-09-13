Examples and recipes
====================

The repository's executable notebooks live in ``examples/notebooks``. The
recipes below are intentionally small enough to adapt in a script or notebook.
For complete, test-backed portfolio-risk recipes, see :doc:`risk_examples`.

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
number. ``CostSensitivityRunner`` executes each immutable case independently,
requires the same finite metrics from every case, and records canonical
assumptions plus input, strategy, and case fingerprints on every result row.
Use ``CostSensitivityVariant`` to label paired buffered/unbuffered cases; the
runner reports the observed difference without imposing a monotonic cost or
performance relationship.

.. literalinclude:: ../../examples/risk/cost_sensitivity.py
   :language: python
   :linenos:

The example evaluator is a controlled offline fixture. In research, its callback
must construct and run a fresh strategy for each case, applying every recorded
slippage, fee, commission, participation, and buffer assumption. Reusing one
realized trade path and merely subtracting scaled costs is not a sensitivity
backtest because costs and participation can change fills, risk decisions, and
later signals.

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
