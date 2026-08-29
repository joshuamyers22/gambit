Risk, stress, and controls
==========================

Pre-trade policies
------------------

Risk policies run after a rule proposes an order and before execution. Combine
small policies rather than embedding every constraint in one rule::

   builder.add_risk_policy(gambit.MaxOrderQuantity(100))
   builder.add_risk_policy(gambit.MaxPositionQuantity(500))
   builder.add_risk_policy(
       gambit.MaxVolumeParticipation(
           0.05,
           volume=lambda order, timestamp: latest_volume[order.contract.symbol],
       )
   )

Rejected proposals remain auditable through immutable ``OrderDecision`` records.
A policy is part of the simulation and must use only information available at
its decision timestamp.

Instrument metadata
-------------------

Attach economic metadata at contract creation::

   spec = gambit.InstrumentSpec(
       asset_class=gambit.AssetClass.FUTURE,
       currency="USD",
       tick_size=0.25,
       exchange_calendar="CME_Equity",
   )
   future = gambit.Contract.create("ESH4", multiplier=50, instrument_spec=spec)

Multipliers affect exposure and P&L. Tick size, expiry, duplicate symbols, and
tradability affect whether an order should be accepted. Use
``InstrumentTradabilityPolicy`` to reject invalid proposals consistently.

Typed measures
--------------

Risk calculations use an explicit context and return long-form Polars data::

   context = gambit.CalculationContext(
       valuation_time=timestamp,
       market_data_as_of=timestamp,
       calendar="NYSE",
       base_currency="USD",
       missing_data_policy=gambit.MissingDataPolicy.ERROR,
   )
   risk = strategy.calculate_risk(
       context,
       [gambit.NetExposureMeasure(), gambit.GrossExposureMeasure()],
   )
   by_asset_class = risk.aggregate(by=("asset_class",))

The valuation timestamp and market-data as-of timestamp are separate to make
stale prices observable.

Stress scenarios
----------------

Scenarios apply composable absolute or relative shocks to matching instruments::

   scenario = gambit.StressScenario(
       "equity-down-10",
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
       [gambit.ScenarioPnlMeasure(scenario)],
   )

Stress P&L is a deterministic revaluation under the specified shock, not a
probability forecast. Maintain scenarios that cover economic mechanisms rather
than tuning them to the strategy's historical loss profile.
