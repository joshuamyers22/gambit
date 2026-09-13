Executable risk examples
========================

The examples in this section use a deterministic two-position portfolio: long
800 shares of ``ACME`` marked at 125 USD and short two ``INDEX-FUT`` contracts
marked at 5,000 USD with a multiplier of 50. They require no network access or
market-data files. Each script contains numeric assertions, is exercised by the
test suite, and can be run from the repository root with::

   python examples/risk/portfolio_exposure.py

These examples demonstrate mechanics and reporting contracts. Their scenarios
and limits are illustrative, not investment or risk-management advice.

Exposure and attribution
------------------------

Start with contract-level marked exposure, then aggregate without losing the
source table. A positive ``net_exposure`` is long economic exposure and a
negative value is short. ``gross_exposure`` is unsigned, so the portfolio below
has 600,000 USD gross exposure despite having -400,000 USD net exposure.

.. literalinclude:: ../../examples/risk/portfolio_exposure.py
   :language: python
   :linenos:

The ``gross_share`` values add to one. They describe the composition of gross
exposure; they are not risk contributions and do not account for covariance.

Scenario stress testing
-----------------------

``StressScenario`` supports shorthand relative shocks keyed by symbol, contract
group, asset class, or ``*``. ``MarketDataShock`` adds explicit matching and can
represent absolute price moves. The example also records a market-data as-of
time one minute before valuation, making the data-age assumption visible.

.. literalinclude:: ../../examples/risk/stress_scenarios.py
   :language: python
   :linenos:

For the uniform 5% decline, the long equity loses 5,000 USD while the short
future gains 25,000 USD. The resulting 20,000 USD gain illustrates why scenario
P&L must preserve position direction and contract multipliers. The
``basis-dislocation`` scenario combines a relative equity decline with a
75-point absolute futures increase.

Composable typed measures
-------------------------

Risk measures return one long-form Polars table. This makes filtering,
aggregation, serialization, and comparison consistent across measure types.

.. literalinclude:: ../../examples/risk/typed_measures.py
   :language: python
   :linenos:

Do not add prices across instruments as an economic total; the ``PriceMeasure``
rows are primarily useful for audit and downstream joins. Exposure and scenario
P&L are additive in this single-currency example. Multi-currency portfolios
require an explicit FX translation policy before aggregation.

Pre-trade controls
------------------

Pre-trade policies evaluate proposals sequentially and stop at the first
rejection. The projected position includes the current position, open orders,
and proposed quantity. This prevents several individually acceptable orders
from collectively bypassing a position limit.

.. literalinclude:: ../../examples/risk/pre_trade_controls.py
   :language: python
   :linenos:

The proposed 250-share order is below the 500-share order limit and below the
10% volume-participation limit. It is nevertheless rejected because 800 held +
100 pending + 250 proposed equals 1,150 shares, above the 1,000-share position
limit. The resulting ``OrderDecision`` retains the rejecting policy, stable
machine-readable code, human-readable message, timestamp, and proposed size.

Covariance risk overlay
-----------------------

This example estimates annualized covariance from deterministic synthetic
returns, reconciles component risk to total volatility, constructs joint
volatility/correlation stress, and calculates a position multiplier from four
portfolio limits.

.. literalinclude:: ../../examples/risk/covariance_overlay.py
   :language: python
   :linenos:

The correlation stress is conditional on current position signs. It moves the
matrix toward the rank-one correlation structure that aligns losses across the
current portfolio, including long/short portfolios where a blanket move toward
positive correlation could reduce rather than increase risk. The overlay is a
control mechanism, not evidence that the covariance model predicts future
returns.

Multi-currency translation
--------------------------

Translate local monetary exposure into the calculation context's base currency
before adding positions or applying covariance. Every FX rate is expressed as
base-currency value per one unit of local currency: an ``EUR`` rate of ``1.20``
under a ``USD`` base means one euro equals 1.20 US dollars.

.. literalinclude:: ../../examples/risk/currency_translation.py
   :language: python
   :linenos:

The translated frame retains local values and attaches the FX rate, timestamp,
and source. This makes the conversion reversible for audit purposes. FX labels
without an actual rate are rejected, as are snapshots newer than the market-data
cutoff. The example uses a fixed synthetic rate; applications should obtain a
point-in-time rate from a controlled data source.

Forecast scaling and combination
--------------------------------

Scale and symmetrically cap long-form rule forecasts before combining them with
fixed weights. The contribution table retains the raw, scaled, capped, weight,
availability, and contribution value for every configured rule:

.. literalinclude:: ../../examples/risk/forecast_combination.py
   :language: python
   :linenos:

Fixed weights must sum to one. ``FixedForecastCombiner.equal`` constructs equal
weights without granting extra scale to duplicated correlated rules. By default,
any unavailable rule stops combination. ``MissingForecastPolicy.ZERO`` retains
the unavailable audit row with zero effective weight and contribution; it does
not silently renormalize the remaining rules. Combined output uses the
``raw_forecast`` column accepted by the existing volatility and VaR sizers.
Historical scalars, estimated weights, and diversification multipliers require
an explicit fit. ``ForecastScalarEstimator`` estimates each rule's scalar as the
configured target mean absolute forecast divided by its observed historical mean
absolute value. It enforces a minimum number of finite observations and rejects
all-zero history instead of inventing a scale. The resulting
``FittedForecastScalars.scale_cap`` creates the same row-level transform.

``ForecastCombinationEstimator`` uses complete historical rows to estimate
inverse-volatility weights and an empirical rule-correlation matrix. It derives
the diversification multiplier as ``1 / sqrt(w' C w)`` and clips that value to
an explicit configured maximum. Constant rules, non-finite observations, and
insufficient complete history fail instead of receiving invented estimates.
Perfectly duplicated rules therefore receive a multiplier of one. The fitted
object returns detached correlation data and builds a fixed combiner, so later
observations cannot revise an earlier result.

Missing rules fail by default. ``ZERO`` preserves fitted weights while assigning
the missing rule zero effective weight. ``RENORMALIZE`` is the explicit opt-in
policy that rescales available positive weights to one; contribution rows retain
both base and effective weights plus the fitted diversification multiplier.

Persist a completed result with ``result.save(path)`` and restore it with
``ForecastCombinationResult.load(path)``. This publishes a canonical versioned
manifest plus separate uncompressed Arrow tables for the combined forecasts and
complete contribution ledger. Both tables are checksummed. Loading validates
their exact schemas, row counts, ordering, finite values, contribution formulas,
and aggregate reconciliation. Existing destinations are never overwritten.

Inside optimization, fit this estimator only through the owned training set:

.. code-block:: python

   scalars = training.fit_forecast_scalars(
       gambit.ForecastScalarEstimator(min_observations=252),
       rule_columns=["carry_forecast", "momentum_forecast"],
   )
   combination = training.fit_forecast_combination(
       gambit.ForecastCombinationEstimator(
           min_observations=252,
           max_diversification_multiplier=2.5,
       ),
       rule_columns=["carry_forecast", "momentum_forecast"],
   )

Both adapters supply only fit rows and fix ``as_of`` to the last fit timestamp.
Rule columns supplied to the combination estimator should contain the historical
scaled/capped forecasts whose joint behavior is being estimated.

Volatility-targeted sizing
--------------------------

Convert relative forecast direction into base-currency exposure with a
portfolio volatility target. The example deliberately uses two passes: first
construct proposed target exposures, then evaluate portfolio limits and apply
their conservative multiplier in a separate sizing call.

.. literalinclude:: ../../examples/risk/volatility_target_sizing.py
   :language: python
   :linenos:

The output retains ``raw_forecast`` and ``target_net_exposure`` alongside final
``net_exposure`` and ``overlay_multiplier``. A risk control therefore cannot
silently rewrite the research signal. Zero forecasts remain zero, and future
covariance estimates, unknown symbols, mixed currencies, and invalid overlay
multipliers fail closed.

Hierarchical control plane
--------------------------

Apply monetary exposure limits after sizing and before converting exposure to
orders. Then enforce operational overrides and rolling trade budgets in the
pre-trade policy chain.

.. literalinclude:: ../../examples/risk/control_plane.py
   :language: python
   :linenos:

Instrument limits run before group, strategy, and portfolio limits. This makes
each later multiplier operate on the exposure actually passed upward and keeps
all earlier limits satisfied. The diagnostic frame records gross exposure
before and after every decision. Persisted override books use a versioned JSON
document and atomic file replacement; the most restrictive matching active
override wins.

VaR, expected shortfall, and VaR sizing
---------------------------------------

Fit historical and Gaussian tail models only through the calculation cutoff,
then use the historical model to scale relative forecasts to a maximum loss
target.

.. literalinclude:: ../../examples/risk/var_and_expected_shortfall.py
   :language: python
   :linenos:

VaR and expected shortfall are positive loss amounts. Historical estimates use
the configured empirical loss quantile and average all observations at or above
that threshold. Gaussian estimates declare their distribution assumption and
use sample mean and standard deviation. Neither method is a forecast guarantee;
keep stress scenarios and hard exposure controls in the decision chain.

Shared deterministic fixture
----------------------------

The examples share this small account constructor. In application code, the
price callback would normally read from a point-in-time market-data service.

.. literalinclude:: ../../examples/risk/common.py
   :language: python
   :linenos:
