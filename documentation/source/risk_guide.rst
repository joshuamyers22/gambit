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

Covariance risk and overlays
----------------------------

Fit covariance only through the market-data cutoff, then express portfolio
volatility in account-currency terms using marked net exposures::

   estimate = gambit.CovarianceRiskModel(
       lookback=252,
       min_observations=120,
       diagonal_shrinkage=0.10,
   ).fit(returns, as_of=context.market_data_as_of)

   risk = gambit.calculate_risk(
       exposures,
       [
           gambit.PortfolioVolatilityMeasure(estimate),
           gambit.ComponentVolatilityMeasure(estimate),
           gambit.DiversificationRatioMeasure(estimate),
       ],
       context,
   )

Component volatility is additive: its instrument rows sum to total portfolio
volatility. Negative components identify positions that reduce estimated risk;
they are not errors. Covariance estimation uses complete rows and reports the
actual final observation in ``estimate.as_of``.

Use stressed covariance and a portfolio overlay to convert breaches into a
single conservative position multiplier::

   stressed = (
       estimate.with_volatility_stress(1.5)
       .with_adverse_correlation_stress(exposures, 0.5)
   )
   overlay = gambit.PortfolioRiskOverlay(
       gambit.PortfolioRiskLimits(
           max_portfolio_volatility=0.10,
           max_stressed_volatility=0.12,
           max_sum_absolute_risk=0.20,
           max_leverage=2.0,
       )
   ).evaluate(exposures, estimate, capital=1_000_000, stressed_estimate=stressed)

The overlay chooses the smallest constraint multiplier but does not mutate
positions or orders. Apply it explicitly in a sizing stage and retain
``overlay.diagnostics`` with the backtest result::

   sizer = gambit.VolatilityTargetSizer(target_volatility=0.10)
   proposed = sizer.size(forecasts, estimate, context, capital=1_000_000)
   overlay = risk_overlay.evaluate(
       proposed.positions,
       estimate,
       capital=1_000_000,
       stressed_estimate=stressed,
   )
   final = sizer.size(
       forecasts,
       estimate,
       context,
       capital=1_000_000,
       overlay=overlay,
   )

``raw_forecast`` determines relative direction and conviction. The sizer
chooses one common scale so annualized portfolio cash volatility equals capital
times the target. It records the unmodified forecast, pre-overlay
``target_net_exposure``, final ``net_exposure``, absolute ``gross_exposure``,
and applied ``overlay_multiplier``. ``pre_overlay_volatility`` and
``achieved_volatility`` are fractions of capital. The returned
``overlay_diagnostics`` retains the constraint calculation. A zero-risk
direction cannot be scaled to a finite target and therefore produces zero
exposure.

Sizing produces continuous monetary exposure, not executable contract
quantities. Contract conversion, lot rounding, liquidity limits, and order
decisions belong to later explicit stages. Covariance risk currently
requires exposures translated into one currency; labels alone are not FX
conversion. Use an explicit snapshot before aggregation::

   fx = gambit.FxRateSnapshot(
       base_currency="USD",
       as_of=context.market_data_as_of,
       rates={"EUR": 1.20, "GBP": 1.35},
       source="closing-fix",
   )
   base_exposures = gambit.translate_exposures(exposures, fx, context)

Rates are base-currency units per one local-currency unit. Translation retains
``local_currency``, the local monetary columns, ``fx_rate``, ``fx_as_of``, and
``fx_source``. Missing currencies, mismatched bases, non-positive rates, and
future snapshots fail before calculation.

Risk-result units
-----------------

Every typed risk row carries a ``unit``. Exposure, scenario P&L, and component
volatility use their calculation currency; diversification ratios use ``ratio``;
raw prices use ``market_price`` because quote conventions are instrument-specific.
``RiskResult.aggregate`` retains measure, scenario, and unit boundaries in
grouped output. An unqualified total requires exactly one measure, scenario,
and unit. This prevents numerically valid but economically meaningless
operations such as adding a market price to currency exposure or net exposure
to gross exposure.
