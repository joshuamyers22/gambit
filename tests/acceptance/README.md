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
mark-to-market, and versioned result persistence. The calendar case uses the
published 2024 US Independence Day closure and adjacent weekdays; it is short
enough to review without treating the calendar adapter as the oracle.

Quantities, statuses, timestamps, schemas, and persisted frames must match
exactly. Currency values use an absolute tolerance of `1e-9` and zero relative
tolerance because the runtime uses binary64 arithmetic; this tolerance is far
below the smallest currency amount in the fixtures and must not scale with P&L.

The corpus schema is versioned independently of the result-bundle schema. Any
fixture change requires a written rationale and review of the manual arithmetic.
Passing this initial corpus is necessary but not sufficient for P0.2: seeded
stateful reconciliation, mutation testing, more calendar boundaries, numeric
failure cases, and the supported hosted interpreter/platform matrix remain open.

