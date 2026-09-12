# Financial acceptance corpus

These fixtures are release evidence, not snapshots copied from Gambit's output.
Expected values in `financial_cases.json` were calculated from the contracts in
`documentation/source/accounting_assumptions.rst`:

- FIFO realized P&L is `matched quantity × signed price change × multiplier`.
- Unrealized P&L applies the same multiplier to each remaining FIFO lot.
- Fees and commissions are cumulative account-currency costs and are not also
  embedded in the fill price.
- Net P&L is cumulative realized plus unrealized minus cumulative costs.
- Equity is starting equity plus net P&L.

The accounting cases deliberately cover long and short entry, scale-out,
cross-zero reversal, multipliers, and separate fees/commissions. The integrated
strategy case adds heartbeat execution lag, a position-limit rejection, final
mark-to-market, and versioned result persistence. Separate cases cover an order
filled over three heartbeats, a two-contract roll with unequal multipliers, and
a day-boundary VWAP whose later prices and volumes cannot change the fill.
Numeric-boundary rows distinguish an intentionally missing NaN mark from invalid
NaN/Inf order, trade, and valuation inputs. Finite-input overflow rows cover
unrealized and realized arithmetic, cumulative costs, cross-contract P&L,
equity, and native quantity range; every case must fail without publishing a
non-finite result. The calendar case uses the published 2024 US Independence Day
closure and adjacent weekdays; it is short enough to review without treating
the calendar adapter as the oracle.

Quantities, statuses, timestamps, schemas, and persisted frames must match
exactly. Currency values use an absolute tolerance of `1e-9` and zero relative
tolerance because the runtime uses binary64 arithmetic; this tolerance is far
below the smallest currency amount in the fixtures and must not scale with P&L.

The corpus schema is versioned independently of the result-bundle schema. Any
fixture change requires a written rationale and review of the manual arithmetic.

`stateful_reconciliation.json` defines four fixed, replayable strategy seeds
across execution lags zero, one, and two. Each run generates 48 heartbeats of
two-contract proposals, partial fills, cancellations, costs, rebates, and FIFO
crossings. The test records the seed, lag, step, and timestamp in ledger failures
and compares every state against a separate test-only FIFO implementation. It
also reconciles original quantity to fills plus remaining quantity, lifecycle
status, immutable decision quantity, trade history, group position, net P&L,
equity, and result telemetry. Changing seeds or bounds is an acceptance-corpus
change, not routine randomization.

Passing these corpora is necessary but not sufficient for P0.2: mutation
testing, more calendar boundaries, and the supported hosted
interpreter/platform matrix remain open.
