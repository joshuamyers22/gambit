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
non-finite result. Five compact NYSE calendar cases cover Independence Day,
exchange-only Good Friday, weekend-observed Christmas, Thanksgiving plus its
early-close Friday, and a New Year boundary. Their expected dates are written
out for review instead of treating the calendar adapter as the oracle. Because
Gambit's calendar API is day-level, the early-close fixture asserts that the day
is open but does not claim session-hour support.

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

Passing these corpora and the targeted mutation gate is necessary but not
sufficient for P0.2: owner review and the supported hosted
interpreter/platform matrix remain open.

Run `make mutation-financial` for the bounded mutation gate. It makes ten
explicit, reviewable changes to `risk.py` and `contract_pnl.py` in an isolated
temporary package and requires the focused risk and accounting suites to kill
each one. Anchors must match exactly once, every mutant must compile, and only an
ordinary pytest failure counts as a kill; collection or infrastructure errors
fail the gate. This targeted score protects inclusive quantity caps, pending
exposure, policy rejection, multipliers, cost signs, and missing-mark behavior.
It is deliberately not presented as a whole-repository mutation score.
