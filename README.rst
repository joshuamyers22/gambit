|PyVersion| |Status| |License|

Introduction
============

The ``gambit`` package is designed for backtesting quantitative strategies. It was originally built for my own use after I could not find a python based framework that was fast, extensible and transparent enough for use in my work.

The goals are:

* Speed - Performance sensitive components are written at the numpy level, or in cython or C++, which can lead to performance gains of a couple of orders of magnitude over Python code.
* Transparency - If you are going to commit money to a strategy, you want to know exactly what assumptions it includes. The code is written and documented so these are as clear as possible.
* Extensibility - It would be impossible to think of all requirements for backtesting strategies that traders could come up with. In addition, it's important to measure custom metrics relevant to the strategy being traded.

Using this framework, you can:

* Create indicators, trading signals, trading rules and market simulators and add them to a strategy
* Create contract groups for PNL grouping. For example, for futures and options, you may create a "front-month future" and "delta hedge" where the actual instruments change over time but you still want to analyze PNL at the contract group level.
* Reuse existing market simulation or add your own assumptions to simulate when and at what price orders are filled
* Measure returns, drawdowns, common return metrics such as sharpe, calmar and also add your own metrics.
* Optimize your strategy's parameters taking advantage of all the CPUs on a machine

Gambit uses Polars for tabular inputs and outputs. Timestamps remain ordinary,
explicit columns rather than an implicit dataframe index.
Statsmodels remains the analytics backend for regression/statistical routines,
and pandas-market-calendars remains the source of exchange schedules. Pandas may
therefore be installed transitively, but it is not Gambit's dataframe API.

Market-data validation
----------------------

``validate_market_data`` checks a Polars DataFrame without modifying it. It can
report missing or unordered timestamps, invalid prices and volumes, large price
changes, future records, and dates outside a named exchange calendar. Validation
returns structured findings so callers can distinguish errors from warnings and
decide whether to reject a run::

   report = gambit.validate_market_data(
       bars,
       price_columns=("close",),
       volume_columns=("volume",),
       calendar_name="NYSE",
       max_price_change=0.25,
   )
   report.raise_if_invalid()

Pre-trade risk policies
-----------------------

Risk policies run after a rule proposes an order and before a market simulator
can fill it. Policies are composable and every proposal produces an immutable
``OrderDecision`` for later audit::

   builder.add_risk_policy(gambit.MaxOrderQuantity(100))
   builder.add_risk_policy(gambit.MaxPositionQuantity(500))

Rejected orders remain in strategy order history with cancelled status, while
``strategy.order_decisions`` records the responsible policy and reason code.

Reproducible runs
-----------------

Every strategy captures its resolved, immutable ``RunConfiguration`` and a
``RunProvenance`` record. The provenance fingerprint incorporates configuration,
package and Git versions, and explicitly registered inputs, but excludes capture
time. ``StrategyBuilder`` automatically fingerprints its Polars input::

   strategy = builder()
   strategy.record_polars_input("features", feature_frame)
   snapshot = strategy.provenance.snapshot()  # JSON-serializable

Configuration files are optional and can be layered with explicit overrides by
calling ``load_run_configuration``. Unknown fields and invalid values are
rejected at load time.

Typed strategy stages
---------------------

Indicators, signals, rules, execution simulators, and accounting implement
structural stage protocols, so plain functions and callable classes remain valid.
``strategy.stage_graph()`` exposes their declared dependencies for tooling and
diagnostics. ``strategy.run()`` validates the graph first and reports missing
dependencies or cycles before any backtest computation begins. It returns an
immutable ``BacktestResult`` containing detached Polars snapshots and run
telemetry. Results can be persisted as an atomic, versioned bundle::

   result = strategy.run()
   result.save("research/run-001.gambit")
   restored = gambit.BacktestResult.load("research/run-001.gambit")

Bundles contain uncompressed Polars IPC tables and a canonical JSON manifest.
The loader verifies each table's SHA-256 digest, row count, schema, and the run
provenance fingerprint before returning data. Saving refuses to overwrite an
existing bundle.

Execution costs and liquidity
-----------------------------

``SimpleMarketSimulator`` retains its original ``slippage_pct`` and
``commission`` arguments and also accepts interchangeable models. Included
models cover percentage slippage, bid/ask spread, square-root market impact,
per-unit and per-order charges, and notional fees::

   simulator = gambit.SimpleMarketSimulator(
       price_function,
       slippage_model=gambit.BidAskSpreadSlippage(0.02),
       commission_model=gambit.PerOrderCharge(1.00),
       fee_model=gambit.NotionalCharge(0.00001),
   )

Use ``MaxVolumeParticipation`` as a pre-trade risk policy when an order should be
rejected rather than assigned impact beyond a configured share of market volume.

Instrument metadata and tradability
-----------------------------------

Contracts can carry an immutable ``InstrumentSpec`` describing asset class,
currency, tick size, exchange calendar, trading timezone, liquidity group, and
tradability state. Duplicate instruments must identify their canonical symbol::

   spec = gambit.InstrumentSpec(
       asset_class=gambit.AssetClass.FUTURE,
       currency="USD",
       tick_size=0.25,
       exchange_calendar="CME_Equity",
   )
   contract = gambit.Contract.create("ESH4", multiplier=50, instrument_spec=spec)

Add ``InstrumentTradabilityPolicy`` to reject expired, bad, duplicate, ignored,
or untradeable instruments before execution. By default, ignored and untradeable
positions may still accept orders that reduce existing exposure.

Risk attribution and stress scenarios
-------------------------------------

``strategy.risk_report`` returns Polars frames containing contract exposure,
grouped attribution, and scenario results. Exposure includes contract multipliers
and preserves the sign of long and short positions::

   scenarios = [
       gambit.StressScenario("risk-off", {"equity": -0.10, "future": -0.05}),
       gambit.StressScenario("market-down", {"*": -0.02}),
   ]
   report = strategy.risk_report(timestamp, scenarios, attribution_by=("asset_class",))
   report.exposures
   report.attribution
   report.scenario_results
   report.summary()

Scenario keys are resolved from most to least specific: symbol, contract group,
asset class, then ``*`` as the portfolio-wide default.

Typed risk measures
-------------------

Risk calculations share a long-form Polars ``RiskResult`` with timestamp,
instrument dimensions, measure, optional scenario, and value columns. Results
can be filtered and aggregated consistently across measures::

   scenario = gambit.StressScenario(
       "equity-down",
       market_shocks=(
           gambit.MarketDataShock(
               gambit.MarketDataPattern(asset_class="equity"),
               -0.10,
               gambit.ShockType.RELATIVE,
           ),
       ),
   )
   result = strategy.calculate_risk(
       timestamp,
       [
           gambit.NetExposureMeasure(),
           gambit.GrossExposureMeasure(),
           gambit.ScenarioPnlMeasure(scenario),
       ],
   )
   result.filter(measure="scenario_pnl").aggregate(by=("scenario",))

Pattern scenarios support symbol, contract-group, asset-class, and currency
matching. Multiple matching absolute and relative shocks are composable, while
the original concise dictionary syntax remains supported.

Calculation context
-------------------

``CalculationContext`` explicitly carries calculation assumptions instead of
relying on mutable global state. Timestamp-only APIs remain supported::

   context = gambit.CalculationContext(
       valuation_time=timestamp,
       market_data_as_of=timestamp,
       calendar="NYSE",
       base_currency="USD",
       scenarios=(scenario,),
       missing_data_policy=gambit.MissingDataPolicy.ERROR,
       provenance_reference=strategy.provenance.run_fingerprint,
   )
   result = strategy.calculate_risk(context, [gambit.NetExposureMeasure()])

Market-data look-ahead is rejected unless explicitly enabled. Historical mode
requires a start and end range. Risk results retain valuation time, market-data
as-of time, and base currency as queryable Polars columns.




Installation
------------

Gambit requires Python 3.10 or newer, a C/C++ compiler, and libzip. On macOS,
install libzip with ``brew install libzip``; on Debian/Ubuntu use
``apt install libzip-dev``. Then install the project in an isolated environment:

::

   python3.12 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .

For development, install every test, documentation, and notebook dependency:

::

   python -m pip install -r requirements-dev.txt
   python -m pytest

Repository layout
-----------------

* ``src/gambit`` contains the installable package and native sources.
* ``tests`` contains automated regression and strategy tests.
* ``examples/notebooks`` contains executable examples and sample data.
* ``tools/migrate_notebooks.py`` records the deterministic pandas-to-Polars
  example migration and clears generated notebook output.
* ``documentation`` contains Sphinx sources and previously generated docs.

Documentation
-------------

The best way to get started is the local ``examples/notebooks/getting_started.ipynb`` notebook.

See ``CONTRIBUTING.md`` for the full validation workflow and
``ADVERSARIAL_REVIEW_PLAN.md`` for the current hardening roadmap.

Disclaimer
----------

The software is provided on the conditions of the simplified BSD license.

.. _Python: http://www.python.org

.. |PyVersion| image:: https://img.shields.io/badge/python-3.10+-blue.svg
   :alt:

.. |Status| image:: https://img.shields.io/badge/status-beta-green.svg
   :alt:

.. |License| image:: https://img.shields.io/badge/license-BSD-blue.svg
   :alt:
