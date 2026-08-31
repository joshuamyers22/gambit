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

Order-state assumptions
-----------------------

Orders begin open. Fills reduce their remaining quantity and move them to
partially-filled or filled status. Rejections are recorded as risk decisions and
cancel the proposed order. Fill-or-kill, day, and good-till-cancelled policies
govern lifetime; a custom simulator remains responsible for actual fill logic.

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
