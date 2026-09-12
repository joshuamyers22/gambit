Building strategies
===================

Builder or direct API
---------------------

:class:`gambit.StrategyBuilder` is the concise interface for one-frame research.
Use :class:`gambit.Strategy` directly when contract groups have different data,
signals depend on other signals, or execution requires a custom simulator.

Vector features
---------------

Prefer Polars expressions for columnar feature engineering. Register the
resulting array as a vector indicator::

   features = bars.with_columns(
       pl.col("close").rolling_mean(20).alias("trend"),
       pl.col("close").pct_change().rolling_std(20).alias("volatility"),
   )
   builder.add_series_indicator("trend", "trend")
   builder.add_series_indicator("volatility", "volatility")

Use a callable indicator when a feature depends on another registered indicator
or needs strategy context. Declare ``depends_on`` so the stage graph can order
the calculation.

Signals are states or events
----------------------------

A state such as ``close > trend`` remains true for many bars. A transition such
as a crossover should normally generate only one event::

   features = features.with_columns(
       (
           (pl.col("close") > pl.col("trend"))
           & (pl.col("close").shift(1) <= pl.col("trend").shift(1))
       ).fill_null(False).alias("crossed_above")
   )

Whether a state or event is correct depends on the rule. A target-position rule
can consume a state every bar; an entry rule usually consumes an event.

Sizing
------

``PercentOfEquityTradingRule`` converts an equity allocation into whole contracts
using ``price * contract.multiplier``. ``BracketOrderEntryRule`` uses the
multiplier-adjusted stop distance when a stop return is supplied, and notional
allocation otherwise. Its optional position cap also uses contract notional.

``VWAPEntryRule`` splits its budget equally across the contract group and returns
every resulting order. With ``stop_price_ind`` it sizes against the monetary
distance to that stop; without it, it uses notional allocation and no stop.
Stops must be finite and strictly below the entry price for longs or above it
for shorts. A valid stop closer than ``min_price_diff_pct`` suppresses entry.

These helpers round toward zero to whole contracts. For example, 10% of 100,000
equity at price 100 and multiplier 50 buys two contracts. Equity fractions must
be finite and non-negative; values above one explicitly request leverage.
Missing entry prices suppress entry, and non-positive or infinite prices are
rejected. Use a custom rule for negative-price instruments or spread premiums.
Zero or negative account equity produces no new entry. These budgets use the
estimated price and exclude execution costs; they do not guarantee a loss limit
after gaps or slippage. Custom rules can incorporate volatility, portfolio
exposure, liquidity, and fees.

Execution costs
---------------

Costs should be explicit and directionally correct::

   simulator = gambit.SimpleMarketSimulator(
       price_function,
       slippage_model=gambit.BidAskSpreadSlippage(spread=0.02),
       commission_model=gambit.PerOrderCharge(amount=1.00),
       fee_model=gambit.NotionalCharge(rate=0.00001),
   )
   builder.add_market_sim(simulator)

Calibrate costs from venue and broker data. A strategy whose result disappears
under a small, defensible cost perturbation is not robust.

Custom rules
------------

A rule is an ordinary callable. Keep it deterministic and return orders rather
than mutating account state::

   def enter_one_unit(
       contract_group,
       i,
       timestamps,
       indicators,
       signal,
       account,
       current_orders,
       context,
   ):
       contract = contract_group.get_contracts()[0]
       return [
           gambit.MarketOrder(
               contract=contract,
               timestamp=timestamps[i],
               qty=1,
               reason_code="MODEL_ENTRY",
           )
       ]

Use ``context`` for immutable run parameters such as calibrated fees. Avoid
closures over mutable notebook state because they weaken reproducibility.

Evaluation
----------

Inspect trades and reconciled P&L before summary ratios. Confirm order reasons,
fill timestamps, quantities, costs, end positions, realized P&L, unrealized P&L,
and equity. Only then evaluate Sharpe ratio, drawdown, or optimization results.

Experimental walk-forward evaluation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``WalkForwardRunner`` to give each refit an explicit warm-up, fit,
validation, and held-out interval. This is an experimental P1.6 boundary; it
does not make optimized research production-qualified.

.. code-block:: python

   config = gambit.WalkForwardConfig(
       fit_size=252,
       validation_size=63,
       heldout_size=21,
       warmup_size=20,
       purge_size=5,
       refit_every=21,
       window=gambit.WalkForwardWindow.ROLLING,
   )
   runner = gambit.WalkForwardRunner(
       chronological_frame,
       timestamp_column="timestamp",
       config=config,
   )
   results = runner.run(fit_model, score_validation, score_heldout)

For parameter selection, ``runner.optimize`` reuses Gambit's existing
``Optimizer`` scheduler. The required ``fit_columns`` allowlist is the exact
schema supplied to candidate fitting and selected-parameter refitting:

.. code-block:: python

   optimized = runner.optimize(
       candidate_parameters,
       fit_candidate,
       score_candidate_validation,
       score_selected_heldout,
       fit_columns=["timestamp", "return", "target"],
       seed=42,
       max_processes=1,
   )

``candidate_parameters(fold, seed)`` returns that fold's finite scalar
parameter mappings. Candidate models are fitted only on the allowed warm-up and
fit columns and ranked by finite validation cost; held-out rows are unavailable
until the winner is selected and refitted. Ties use a canonical parameter
identity rather than process completion order. A fold-specific seed derived
from the base seed and split identity is supplied to every callback. Use
module-level, pickleable callbacks and a static candidate source when selecting
``max_processes > 1``; adaptive generators remain an existing single-process
``Optimizer`` feature.

Sizes are row counts and all intervals are half-open. Timestamps must be
timezone-naive, non-null, strictly increasing, and unique. ``fit_model``
receives the warm-up and fit frames separately; neither validation nor held-out
rows are exposed to it. One purge gap separates fit from validation and another
separates validation from held-out evaluation. Expanding windows preserve the
initial warm-up and grow the fit interval. Rolling windows move a fixed-size
warm-up and fit pair. ``refit_every`` cannot be shorter than the held-out size,
so reported held-out intervals cannot overlap. An incomplete terminal fold is
not evaluated, and data too short for one complete fold fails explicitly.

The runner copies its input, produces stable split identities from the complete
timestamp grid and schedule, and detaches finite validation and held-out metric
mappings. ``fit_columns`` prevents undeclared columns from entering optimized
fits, but it cannot detect whether an allowed column was itself computed using
future information. Callback closures, external data, and arbitrary fitted-
object mutation remain caller responsibilities. P1.6 still requires explicit
built-in transform/scalar/covariance adapters, persisted experiment identities,
failed trials and model/input hashes, and chronological out-of-sample equity.
