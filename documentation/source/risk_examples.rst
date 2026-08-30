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

Shared deterministic fixture
----------------------------

The examples share this small account constructor. In application code, the
price callback would normally read from a point-in-time market-data service.

.. literalinclude:: ../../examples/risk/common.py
   :language: python
   :linenos:
