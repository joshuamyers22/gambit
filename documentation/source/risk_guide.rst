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

Accepted and rejected proposals retain detached, frozen ``OrderDecision.snapshot``
records (``OrderSnapshot``). Use the snapshot for historical symbol/group,
submission time, quantity, order type, time-in-force, status, reason, and built-in
limit/VWAP/roll terms. ``OrderDecision.order`` remains the live operational order
for compatibility: its quantity and status change during execution, and its
contract reference can be reassigned. It is not an immutable audit record.
Arbitrary user properties and custom order-subclass terms are not captured;
engine-generated roll identity and leg markers are captured explicitly.
A policy is part of the simulation and must use only information available at
its decision timestamp.

``MaxPositionQuantity`` checks independent fill exposure for each symbol. With
current position ``p``, reachable positions range from ``p + sum(sell quantities)``
to ``p + sum(buy quantities)``, including the proposal and remaining open orders.
A buy must leave the upper endpoint at or below the cap; a sell must leave the
lower endpoint at or above the negative cap. Opposite pending orders provide no
offsetting credit. For example, a pending sell of 100 cannot justify buying 200
against a cap of 100 from a flat account.

An existing breach on the opposite side does not block a reducing order, but
the order may not create a new opposite-side breach. A position of +10 with cap
5 can sell 2 (reducing the breach) or 15 (ending at -5), but cannot sell 16.
Existing unsafe pending exposure is not automatically cancelled by this policy;
owners must resolve it. Cancellation requests retain their remaining exposure
until cancellation is acknowledged. Filled/cancelled orders no longer reserve
exposure. Roll legs are admitted separately, with no atomic/netting exemption:
there is no verified all-or-none fill-group contract across custom simulators.
The existing ``RiskContext.projected_position`` remains a net-all-fills helper
for other/custom policies; use ``position_bounds`` for independent-fill endpoints.

New strategy results persist snapshot fields in bundle version 4. Versions 2
and 3 remain readable with their original decision columns; missing historical
terms are not reconstructed from terminal orders, including when re-saving a
legacy result. Check column availability before using the additional fields.

Use ``gambit.risk.decide_order`` for standalone pre-trade decisions. Before any
policy runs, it checks the proposal and all still-open context quantities as
finite, nonzero whole units, even with no policies configured. Invalid inputs
raise ``ValueError`` rather than an accepted/rejected ``OrderDecision``; fix the
inputs instead of treating validation failure as a limit decision. Partial fills
and cancellation requests still count as open exposure. Filled/cancelled context
orders may retain zero quantity. Signed and NumPy quantities and unscheduled
proposal timestamps remain supported. Direct ``policy.evaluate`` calls do not
run this shared preflight. It does not replace rule admission, validate every
order field or guarantee the correctness of custom policy calculations.

Hierarchical exposure limits
----------------------------

Apply limits to proposed base-currency monetary exposure after volatility
targeting and portfolio overlays::

   limited = gambit.HierarchicalExposureLimiter(
       [
           gambit.ExposureLimit(gambit.ControlLevel.INSTRUMENT, 250_000, "AAPL"),
           gambit.ExposureLimit(gambit.ControlLevel.GROUP, 750_000, "equities"),
           gambit.ExposureLimit(gambit.ControlLevel.STRATEGY, 1_000_000, "trend"),
           gambit.ExposureLimit(gambit.ControlLevel.PORTFOLIO, 2_000_000),
       ]
   ).apply(proposed_positions)

Input rows require ``symbol``, ``contract_group``, ``strategy``, and
``net_exposure``. Children are clipped proportionally before their parents.
The result preserves ``pre_limit_net_exposure`` and records the cumulative
``limit_multiplier`` and final gross exposure. Unmatched or duplicate limit
identities fail as configuration errors instead of silently doing nothing.

Operational overrides and trade budgets
---------------------------------------

Reduce-only and no-trade state is separate from research forecasts and sizing.
Override books can target the whole portfolio or a strategy, group, or
instrument::

   book = gambit.TradingOverrideBook(
       [
           gambit.TradingOverride(
               gambit.ControlLevel.INSTRUMENT,
               gambit.TradingMode.NO_TRADE,
               effective_from=timestamp,
               reason="exchange halt",
               key="AAPL",
           )
       ]
   )
   book.save("run-state/trading-overrides.json")
   policy = gambit.TradingOverridePolicy(
       gambit.TradingOverrideBook.load("run-state/trading-overrides.json"),
       strategy="trend",
   )

Persistence uses a versioned JSON schema and writes a fully flushed temporary
file before atomic replacement. Active overrides are inclusive of their start
and optional expiry timestamps. When multiple scopes match, no-trade outranks
reduce-only; a more specific scope breaks ties. Reduce-only orders must lower
absolute projected position after pending orders.

Use ``RollingTradeBudget`` in the same policy chain to limit churn::

   builder.add_risk_policy(
       gambit.RollingTradeBudget(
           maximum_quantity=10_000,
           window=np.timedelta64(1, "D"),
           level=gambit.ControlLevel.GROUP,
           key="equities",
       )
   )

The budget counts absolute executed quantity inside the trailing window,
remaining quantity on open matching orders, and the proposal. It intentionally
does not net buys against sells. Quantity budgets do not substitute for
notional, liquidity, or exposure limits when contract sizes differ.

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

Value at risk and expected shortfall
------------------------------------

Tail-risk models use aligned, complete return rows no newer than the explicit
cutoff::

   historical = gambit.TailRiskModel(
       lookback=500,
       min_observations=250,
       confidence=0.99,
       horizon_days=1,
       method=gambit.TailRiskMethod.HISTORICAL,
   ).fit(returns, as_of=context.market_data_as_of)

   gaussian = gambit.TailRiskModel(
       lookback=500,
       min_observations=250,
       confidence=0.99,
       horizon_days=10,
       method=gambit.TailRiskMethod.GAUSSIAN,
   ).fit(returns, as_of=context.market_data_as_of)

   tail_risk = gambit.calculate_risk(
       base_currency_exposures,
       [
           gambit.PortfolioVaRMeasure(historical),
           gambit.PortfolioExpectedShortfallMeasure(historical),
       ],
       context,
   )

Results use a positive-loss convention: a value at risk of 50,000 USD means
the configured quantile loss is 50,000 USD, not negative P&L. Historical
multi-day horizons use overlapping sums of fixed-exposure daily P&L. Gaussian
horizons scale the sample mean linearly and standard deviation by the square
root of time. Expected shortfall is the conditional tail mean and cannot be
below VaR.

Duplicate timestamps, mixed currencies, unknown instruments, non-finite rows,
and insufficient complete observations fail closed. The fitted model retains
the final usable timestamp, not merely the final input timestamp. Both methods
assume current fixed exposures across the horizon and omit transaction costs,
liquidity, regime changes, and nonlinear option repricing.

VaR-targeted sizing
-------------------

Use the same fitted historical or Gaussian model to convert relative forecasts
into exposure::

   sizer = gambit.VaRTargetSizer(target_var=0.02)
   proposed = sizer.size(
       forecasts,
       historical,
       context,
       capital=1_000_000,
   )

``target_var`` is a fraction of capital at the fitted model's confidence and
horizon. The output retains ``raw_forecast``, pre-overlay
``target_net_exposure``, final ``net_exposure``, and achieved VaR. An optional
``PortfolioRiskOverlayResult`` is applied afterward as a separate multiplier,
just as in volatility targeting. If the forecast direction has zero modeled
VaR, the sizer returns zero exposure rather than dividing by zero or inventing
risk capacity.
