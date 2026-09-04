# Native multi-instrument tick backtest: brief and execution budget

Status: proposed product contract; replay infrastructure under development.
Owner/decision authority: Josh Myers. Review date: 2026-09-04.

Reference baseline: `production-project-template` commit `e132c6e`, specifically
`docs/LATENCY_SENSITIVE_CPP_GUIDE.md`, `templates/PROJECT_BRIEF.md`,
`templates/LATENCY_BUDGET.md`, and `docs/CPP_SOURCE_REVIEW.md`. The executable
C++ archetype is an ownership/validation/build reference, not an execution model.

## Outcome and measurement boundary

Run one complete tick-level, multi-instrument strategy over two to three years
in a few seconds after reusable order-book preprocessing. The initial workload
is 10 **aggregate** events/second across eight instruments, not 10 per instrument:
631,584,000 events for 2023–2024 and 946,944,000 events for 2023–2025.

Proposed acceptance objective: repeated warm runs have a p95 complete execution
time at or below **5 seconds** on a named reference host. This translates to
189,388,800 events/second at three years. The number is a target, not demonstrated
capability, and must be approved with the strategy and execution model.

The timer starts with validated, preprocessed input resident and initialized
configuration available. It includes event traversal, strategy decisions,
order/risk state transitions, fills, fees, cash/positions/P&L, and final immutable
result construction. Report per-run state initialization separately until its
cost is measured, then include it in the accepted run boundary. Never reuse a
previous trial's terminal portfolio state.

Report preprocessing, cold data loading, warm-up, execution, and serialization
separately. Disk loading, Python-oracle validation, and reusable preprocessing
are not hidden inside a claimed five-second execution time. A memory mapping
alone does not establish residency; cold faults must be measured separately.

## Proposed five-second stage budget

These allocations are planning constraints, not independent p95 values that can
be added to manufacture an end-to-end percentile. Measure the full distribution.

| Stage | Planning allowance | Required behavior |
| --- | ---: | --- |
| Initialization and input traversal | 0.75 s | Bounded state, no live transport, causal event visibility |
| Strategy and portfolio-risk decisions | 1.50 s | No per-event Python callbacks; controls retained |
| Order lifecycle and fill simulation | 1.50 s | Explicit fill/latency model; no silent fidelity reduction |
| Accounting and result construction | 0.75 s | Correct fees, units, positions, cash, P&L, and audit records |
| Headroom | 0.50 s | Measured variance, not permission to skip work |

Record p50/p95/p99/max, run count, warm-up, CPU and wall time, memory, compiler
flags, CPU topology, input/configuration hashes, order/fill counts, and build
identity. Do not label a tiny sample's extreme percentiles statistically robust.
Shared CI validates benchmark correctness; timing acceptance belongs on a
controlled reference host. The currently measured host is Apple Silicon/macOS
15.5; precise hardware and compiler identity remain acceptance prerequisites.

## Invariants and preprocessing limits

- Preserve source sequence, exchange time, receive time, stable tie-breaking,
  and the before/after-event visibility rule. Never expose future observations.
- Define gap, duplicate, stale-book, and out-of-order recovery policy explicitly;
  preprocessing must not conceal invalid feeds by silently sorting or deleting.
- Cache only reusable market-derived state/features with versioned schemas,
  transforms, units, calendars, symbology, and hashes. Parameter-dependent
  features need parameter-specific cache identity.
- Fills, queue position, capital constraints, and impact dependent on simulated
  orders remain runtime policy unless a proven equivalent transform exists.
- Keep a single explicit owner for shared cash/risk/order state. Independent
  parameter trials can run in parallel; instruments sharing a portfolio cannot
  be split without preserving cross-instrument event ordering and state.
- Choose price/quantity scales, rounding, overflow, fee/funding precision, and
  P&L accounting deliberately. Do not silently replace existing floating-point
  semantics or approximate checks to satisfy a timer.
- Reproducibility means identical decisions for identical logical inputs,
  configuration, and controlled build/arithmetic environment. Any numeric
  tolerance must be stated separately from exact order/fill agreement.
- Do not skip market events unless equivalence is proved for all affected
  strategy, execution, accounting, and risk state, including pending timers.

## Resource and failure contract

Preallocate bounded instrument, active-order, and pending-event state. Record
capacity limits and fail explicitly on exhaustion; never drop ticks or orders
to stay inside the latency objective. Retain order/fill audit records without
per-tick Python containers. Audit volume and result materialization count toward
the workload and execution budget. Cancellation ends at a defined safe boundary
and cannot publish an incomplete run as a valid backtest.

The immutable input mapping, memory budget, maximum instruments/active orders,
audit capacity, cancellation bound, and cold-load performance remain to be set
from the chosen strategy and data. No hard real-time or live-trading claim is made.

## Small, reversible delivery sequence

1. Characterize the Python oracle and native ring factor path; preserve evidence.
2. Add direct contiguous-array native replay, sharing the existing arithmetic.
   This is a transport optimization, **not an end-to-end backtester**.
3. After the execution-model decision, implement one compiled strategy with
   shared portfolio state, orders, fills, accounting, and an independent oracle.
4. Validate small hostile corpora and full replay; compare complete order/fill
   traces and terminal portfolio state, not just a checksum or aggregate P&L.
5. Profile the entire path, make one optimization at a time, and retain before/
   after distributions plus correctness, sanitizer, and rollback evidence.

## Open decisions

| Decision | Status / authority |
| --- | --- |
| Execution model | User selected top-of-book execution on 2026-09-04; no queue-position simulation |
| Representative strategy, expected order/fill frequency, active-order bounds | Required before end-to-end acceptance |
| Fee/funding, accounting precision, and market-impact assumptions | Required before execution-policy implementation |
| Reference hardware, complete timer boundary, repetition count, approved threshold | Proposed above; not yet accepted |

## Existing evidence and limitations

The three-year factor-only replay processed 946,944,000 synthetic events with
Python parity within declared tolerance, no dropped ticks, and no sequence errors.
It took 37.09 seconds in the native ring/factor path. It did **not** create
strategy orders, simulate fills, or run portfolio accounting. See
`../performance/crypto_tick_parity_2026-09-04.md` and its raw JSON.

No book, template microbenchmark, or factor-only speedup establishes the target.

## Characterization prototype scope

The initial `gambit.tick_backtest.TopOfBookBacktester` is experimental and is not
a replacement for `Strategy`. Its compiled strategy alternates long/flat targets
every configured number of instrument observations. This is a reproducible
execution workload, not a recommended trading strategy or a general strategy API.

It supports market orders, one active order per instrument, partial fills capped
by that event's opposing displayed size, a receive-time latency floor, shared
cash checks before each fill, integer monetary accounting with rounded-up fees,
and terminal bid marking. New orders cannot fill on their creation event. Each
new quote supplies a refreshed liquidity budget: persistent effects of our own
trades are not modeled. Outstanding orders remain outstanding at the end;
terminal liquidation and exit fees are not invented.

Current exclusions: limit orders, atomic rolls, shorts/leverage, funding, FX,
queue priority, own-market impact, existing Python risk-policy callbacks, and
arbitrary user strategies. Existing Gambit order/risk APIs remain unchanged.
These exclusions must stay visible; this prototype cannot accept an unsupported
order type through a compatibility fallback.

The prototype's sequence is a global, contiguous replay ordinal starting at
zero, not a substitute for validating per-venue feed sequence during preprocessing.
Inputs use one common price-tick and quantity-lot scale and quote currency.
Reference tests compare every order and fill as well as cash, positions, fees,
and P&L using exact integer equality. Large synthetic runs currently reconcile
the entire fill ledger; independent Python trace parity at the complete
three-year volume remains separate, outstanding evidence.
