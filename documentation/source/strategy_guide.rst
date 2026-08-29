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

``PercentOfEquityTradingRule`` converts an equity allocation into whole units at
the estimated entry price. This is allocation sizing, not volatility or loss
sizing. ``BracketOrderEntryRule`` sizes against a stop distance. Custom rules can
use contract multipliers, volatility, portfolio exposure, and liquidity.

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
