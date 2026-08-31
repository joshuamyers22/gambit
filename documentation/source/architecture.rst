Architecture and event flow
===========================

Gambit is an event-driven research engine with vectorized feature preparation.
Its design separates calculations that can be performed over an entire column
from decisions that must observe point-in-time account and order state.

System boundaries
-----------------

The principal data flow is::

   Polars/NumPy market data
              |
              v
      indicators -> signals      (vector calculations)
              |         |
              +----+----+
                   v
          stage dependency graph
                   |
                   v
   heartbeat -> rules -> risk policies -> open orders
       ^                                  |
       |                                  v
       +---- account <- trades <- market simulators
                   |
                   v
       immutable BacktestResult
                   |
                   v
       checksummed result bundle

The :class:`~gambit.Strategy` owns orchestration and the ordered heartbeat. Stage
callables own calculations but do not own the event loop. Contracts and contract
groups provide instrument identity. The account owns positions and P&L. Market
simulators alone convert accepted orders into trades. A
:class:`~gambit.BacktestResult` detaches output tables from mutable run state.

Run-level sequence
------------------

Calling :meth:`gambit.Strategy.run` performs these measured phases:

#. Validate stage names, dependencies, and cycles.
#. Calculate indicators in dependency order for the selected contract groups.
#. Calculate signals in dependency order.
#. Generate scheduled rule invocations and execute the heartbeat loop.
#. Calculate explicitly requested risk, stress, and validation analytics.
#. Clone orders, decisions, trades, P&L, analytics, provenance, and telemetry into
   an immutable result snapshot.

Indicator and signal arrays are therefore fixed before event iteration begins.
Rules cannot change an earlier vector calculation by placing a trade. A rule
that needs account state receives the account explicitly and runs inside the
heartbeat instead of masquerading as a vector signal.

One heartbeat
-------------

For heartbeat index ``i``, orchestration is::

   expire/cancel eligible open orders
                 |
                 v
   run market simulators for eligible open orders
                 |
                 v
   add returned trades to the account
                 |
                 v
   for each scheduled rule, in registration order:
       call rule
       validate returned orders
       evaluate risk policies in registration order
       retain accepted orders; cancel rejected orders
       if trade_lag == 0:
           run market simulators immediately
           add returned trades to the account
                 |
                 v
   remove orders no longer open

This ordering has deliberate consequences:

* Existing eligible orders fill before rules inspect the current position.
* With ``trade_lag == 0``, each rule can affect the position seen by a later
  rule at the same timestamp.
* With a positive lag, rules at a timestamp see earlier fills but not fills from
  orders proposed during that timestamp.
* Registration order is observable for rules, risk policies, and simulators.
  Reordering them can change results and should be treated as a model change.

Execution eligibility
---------------------

An order becomes eligible when the current heartbeat index minus its placement
index reaches ``trade_lag``. Fill-or-kill orders are cancelled after their
eligible heartbeat if still open. Day orders are cancelled once the heartbeat
date is later than the placement date. Cancel-requested orders are cancelled
before simulation.

A simulator must return trades for objects in the current open-order set. Each
trade must reference the same contract as its order and use the current strategy
timestamp. Gambit validates those relationships before accounting receives the
trade. Partial fills mutate the order quantity to the unfilled remainder; the
trade table is the authoritative record of executed quantities.

Component ownership
-------------------

.. list-table::
   :header-rows: 1

   * - Component
     - Owns
     - Must not assume
   * - Indicator/signal stage
     - Vector feature or decision-state calculation
     - Future values are available at an earlier timestamp
   * - Rule
     - Order proposals from point-in-time inputs
     - A proposal will pass risk or fill
   * - Risk policy
     - Accept, resize, or reject decision
     - Execution price or future portfolio state
   * - Market simulator
     - Fill eligibility, price, quantity, and costs
     - A market order guarantees liquidity
   * - Account
     - FIFO lots, positions, cumulative P&L, and equity
     - Corporate actions, funding, or FX conversion occur automatically
   * - Result bundle
     - Detached auditable outputs and provenance
     - External source data can be reconstructed unless it was recorded

Failure boundaries
------------------

Stage dependency errors fail before feature calculation. Invalid rule and market
simulator outputs fail at the current index with a
:class:`~gambit.BacktestCallbackError` chained from the original exception.
Risk decisions are retained whether accepted or rejected. Persistence validates
schema, bounds, and checksums independently of strategy execution.

The native tick ring and NVMe-mapped factor cache are optional research
accelerators. They do not alter strategy ordering or accounting semantics. A
cached factor is reusable only when its versioned identity, lineage, parameters,
and input fingerprint match.
