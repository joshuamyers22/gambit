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
