Accounting and execution assumptions
====================================

This page defines what Gambit's backtest account means. These are model
contracts, not conventions to infer from a performance chart.

Units and signs
---------------

Order, trade, and position quantities are signed instrument units. Positive is
long or buy; negative is short or sell. Prices and quantities must be finite at
execution. A contract's ``multiplier`` converts one price-unit move in one
instrument unit into account-currency P&L.

The core account has one account currency. It does not automatically translate
foreign-currency fills, cash balances, or P&L. Use explicit conversion inputs
and the currency-risk facilities when instruments span currencies.

FIFO lot accounting
-------------------

Trades close opposite-signed open lots in first-in, first-out order. For a
matched quantity ``q`` from an open lot, realized P&L is::

   long lot:   q * (exit_price - entry_price) * multiplier
   short lot:  q * (entry_price - exit_price) * multiplier

A trade larger than the open position closes every eligible FIFO lot and opens
the residual quantity at the trade price. Same-sided trades remain distinct
lots for future FIFO matching. Zero-quantity events are ignored by the native
FIFO calculator and public order types reject zero quantities.

Mark-to-market
--------------

For the remaining signed open quantity, Gambit reports::

   unrealized = open_quantity * (mark_price - weighted_open_price) * multiplier
   net_pnl    = realized + unrealized - cumulative_commission - cumulative_fee
   equity     = starting_equity + aggregate_net_pnl

``weighted_open_price`` summarizes the still-open FIFO lots. It is weighted by
their signed quantities; valid account state does not mix long and short open
lots for one contract after netting.

When an open position has no mark because the price callback returns ``NaN``,
Gambit carries forward the previous unrealized P&L. It does not force the mark
to zero and does not liquidate the position. Infinite marks and non-real values
are errors. A closed position has zero unrealized P&L.

Costs and cash
--------------

Fees and commissions are finite signed amounts supplied on each simulated trade
and accumulated in account currency. Positive values reduce net P&L; negative
values represent rebates and increase it. The
account does not maintain a double-entry cash ledger: trade notional is not
subtracted from a cash balance, and leverage or margin is not inferred from
cash. Starting equity is a positive research notional used for equity and return
calculations.

Slippage is represented in the simulated fill price, while commissions and fees
are separate trade fields. Do not also embed the same cost in the fill price or
it will be counted twice.

Timing assumptions
------------------

Trades affect positions as soon as the simulator returns them. Accounting can
calculate at configured daily timestamps and at an explicitly requested final
timestamp. A fill at timestamp ``t`` participates in P&L at ``t``. Marks are
looked up on the strategy heartbeat, not interpolated.

``trade_lag`` is measured in heartbeat indices rather than clock duration. A lag
of one means the next available event, which may be one second, one session, or
another interval depending on the input grid. Same-bar execution with lag zero
is only causally valid when the execution price was available after the decision
or comes from a separately modeled quote.

An order submitted at heartbeat index ``j`` cannot reach a market simulator
before index ``j + trade_lag``. Orders still waiting at the end of the run are
not force-filled. Each simulator receives only eligible, still-open orders in
submission order; partial fills remain eligible on later heartbeats without
restarting the lag. Callbacks still run with an empty order tuple when no orders
are eligible. The account accepts reported fills only for the eligible tuple.

Order-state assumptions
-----------------------

Orders begin open. Fills reduce their remaining quantity and move them to
partially-filled or filled status. Rejections are recorded as risk decisions and
cancel the proposed order. Fill-or-kill, day, and good-till-cancelled policies
govern lifetime; a custom simulator remains responsible for actual fill logic.

Roll commands are expanded into outgoing and incoming market legs in that order.
Expansion rechecks distinct contracts in the same group and finite, nonzero,
whole-unit quantities with opposite signs, even if the command changed after
construction. Unequal leg sizes are valid. Only open commands can expand;
cancelled or already-progressed commands cannot create fresh open legs. An
invalid roll rejects the entire rule-return batch before risk decisions or
accounting. This validation does not guarantee all-or-none execution across
custom simulators or independent risk decisions on the expanded legs.

Cancellation requests are acknowledged on the next market-simulation pass,
including while an order is waiting out its lag. DAY orders expire when the
heartbeat's NumPy calendar date advances past the submission date, even before
eligibility; this is not an exchange-session calendar. GTC orders can wait across
that boundary. FOK orders retain the existing fill window at exactly
``j + trade_lag`` and are cancelled on a later heartbeat if still open; this
engine lifetime policy does not enforce a custom simulator's all-or-none fills.

Each reported fill must have a finite, nonzero whole-unit quantity with the same
sign as its originating order. The aggregate quantity returned for that order
by one simulator must not exceed the remaining quantity captured before the
callback. Opposing fills cannot net against each other to evade that bound.
These checks apply whether the callback calls ``Order.fill()``, directly assigns
order state, or leaves fill application to the engine. Valid split fills retain
callback order. An invalid batch is rejected before account mutation and the
eligible orders' quantities/statuses are restored; valid fills from earlier
simulator callbacks are not rolled back. This is a Strategy callback-boundary
guarantee, not a new validation policy for direct ``Account.add_trades()`` calls.

Trade numeric fields remain mutable, so both the simulator-return boundary and
``Account.add_trades()`` recheck their construction-time invariants: quantities
are finite, nonzero whole units; price, fee and commission are finite real
numbers, not booleans or numeric strings. Finite negative prices and negative
charges (rebates) are permitted. A direct-import batch with invalid numbers is
rejected before any ledger updates, preserving earlier history and valuation.
This does not make direct account imports enforce the Strategy's execution-lag,
remaining-order-quantity, or fill-direction policies.

Both ingestion boundaries also recheck the construction-time reference and
chronology contract: a trade has a real ``Contract`` and ``Order``, the contract
matches its order, both timestamps are valid NumPy datetimes, and execution does
not precede submission. The account still requires the execution timestamp on
its grid, but a historical order may have an earlier off-grid submission time
and may already be filled.

Rule and market-simulator callbacks cannot change a pending order's contract
reference, submission timestamp (including its NumPy unit), or time-in-force.
Rules may request or apply cancellation but cannot resize or fill pending orders.
Simulators may apply fills/cancellations consistent with their validated trade
results; lag-ineligible orders must remain unchanged. Risk-policy evaluation is
read-only for these fields plus quantity/status on both proposed and pending
orders. Violations fail before publishing that callback's results.

On callback failure or interruption, the protected contract reference, timestamp,
time-in-force, quantity and status are restored to their pre-callback values.
Previously committed simulator fills remain committed. These are scoped guards,
not globally frozen objects or an arbitrary-Python sandbox: custom properties,
reason codes, order-type-specific terms, shared contract internals and external
callback state are not deep-restored. Other callback kinds and mutation outside
these boundaries are not covered. Use a fresh strategy after a failed callback
that changed unprotected state.

Earlier general-engine results with ``trade_lag > 1`` may contain premature
next-heartbeat fills and must be rerun after the eligibility correction. This
does not change the experimental native execution models.

The result's trade rows contain executed quantities. An order object's quantity
is mutable remaining quantity, so order output should be interpreted alongside
status, decisions, and trades rather than as an immutable original-size ledger.

Not modeled automatically
-------------------------

The core account does not invent assumptions for:

* dividends, splits, coupons, borrow fees, funding, or interest;
* futures variation margin, initial margin, or maintenance margin;
* FX conversion or settlement timing;
* tax lots other than FIFO;
* exchange priority, queue position, or hidden liquidity;
* forced liquidation, option exercise, or assignment; or
* stale-mark haircuts and valuation reserves.

Represent a relevant effect in adjusted data, explicit cash-flow/cost logic, a
custom simulator, or a specialized accounting layer. Document it in run
provenance so two economically different simulations cannot be mistaken for the
same experiment.

Required reconciliation
-----------------------

Before accepting a result, verify at minimum:

#. Trade quantities reconcile to position changes by symbol.
#. FIFO realized P&L agrees with an independent ledger for representative paths.
#. Open positions reconcile to unrealized P&L at the final mark.
#. Fees and commissions reconcile to trade-level costs.
#. ``ending equity - starting equity`` equals aggregate final net P&L within the
   declared floating-point tolerance.
#. Signal, order, and fill timestamps respect the intended information lag.

Gambit's golden accounting and randomized independent-oracle tests enforce these
invariants for the built-in account, including long, short, partial close,
cross-zero, multiplier, and cost scenarios.
