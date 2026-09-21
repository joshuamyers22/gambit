# Native multi-instrument tick backtest: brief and execution budget

Status: experimental product brief; engineering contract v1 approved 2026-09-20.
Owner/decision authority: Josh Myers. Original brief date: 2026-09-04.

Reference baseline: `production-project-template` commit `e132c6e`, specifically
`docs/LATENCY_SENSITIVE_CPP_GUIDE.md`, `templates/PROJECT_BRIEF.md`,
`templates/LATENCY_BUDGET.md`, and `docs/CPP_SOURCE_REVIEW.md`. The executable
C++ archetype is an ownership/validation/build reference, not an execution model.
Gambit's governing engineering contract is
[`LATENCY_BUDGET.md`](../../LATENCY_BUDGET.md), `gambit-fifo-latency-v1`, approved
under user delegation on 2026-09-20. It fixes the synthetic FIFO workload, M4 host,
prepared-chunk execution sum, full-harness timer, statistical gates and resources.
The original product proposal below remains context; v1 supersedes its unresolved
engineering choices. Production qualification and measured acceptance remain open.

## Outcome and measurement boundary

Run one complete tick-level, multi-instrument strategy over two to three years
in a few seconds after reusable order-book preprocessing. The initial workload
is 10 **aggregate** events/second across eight instruments, not 10 per instrument:
631,584,000 events for 2023–2024 and 946,944,000 events for 2023–2025.

Approved v1 engineering objective: p95 **prepared-chunk execution sum ≤5 seconds**
on `m4-local-v1`; see the governing contract for the 200-trial decision rule.
This translates to 189,388,800 events/second at three years. The objective is
not demonstrated capability or a continuous storage-to-result wall-time promise.

The accepted execution sum includes fresh initialization, timed batch calls
(validation, traversal, strategy, FIFO, cash/fees/accounting), and immutable result
materialization. Input generation/loading and hashes are outside batch timers
and inside their separately defined full-harness boundaries. Never reuse a
previous trial's terminal portfolio state or imply that the 83.3 GB is resident.

Report preprocessing, cold data loading, warm-up, execution, and serialization
separately. Disk loading, Python-oracle validation, and reusable preprocessing
are not hidden inside a claimed five-second execution time. A memory mapping
alone does not establish residency; cold faults must be measured separately.

## Five-second diagnostic stage budget

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
controlled reference host. The v1 host and compiler identity are fixed in `LATENCY_BUDGET.md`; the
full qualification campaign has not run.

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

V1 sets eight instruments, eight active orders, one-million-row audit bounds,
a 512 MiB replay-process RSS budget, bounded chunks and cancellation/watchdogs
in `LATENCY_BUDGET.md`. LAT-02 runner enforcement is implemented and locally
verified; qualification evidence remains open. Absolute cold-load
performance and representative production data remain outside synthetic v1. No hard real-time or live-trading claim is made.

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
| Execution model | User selected top-of-book execution, then approved opt-in conservative FIFO queue-position testing on 2026-09-04; see `fifo_queue_execution.md` |
| Synthetic strategy, order/fill mix and active-order bounds | Approved in v1; representative production strategy/data remain open |
| Fee/funding, accounting precision, and market-impact assumptions | V1 fixes integer units/rounded fees and excludes funding/impact; production calibration remains open |
| Reference hardware, timer boundary, repetition count and thresholds | Approved in v1; measured attainment remains open |

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

The opt-in FIFO mode additionally supports same-side best-price resting limits
and conservative volume-ahead tracking; the market mode remains the default.
Its input, arrival, cancellation and matching contract is in `fifo_queue_execution.md`.

Current exclusions: general limit-order placement, atomic rolls, shorts/leverage, funding, FX,
actual venue queue reconstruction, own-market impact, existing Python risk-policy callbacks, and
arbitrary user strategies. Existing Gambit order/risk APIs remain unchanged.
These exclusions must stay visible; this prototype cannot accept an unsupported
order type through a compatibility fallback.

The prototype's sequence is a global, contiguous replay ordinal starting at
zero, not a substitute for validating per-venue feed sequence during preprocessing.
Inputs use one common price-tick and quantity-lot scale and quote currency.
Reference tests compare every order and fill as well as cash, positions, fees,
and P&L using exact integer equality. LAT-04 completed
[independent full-volume trace parity](../performance/fifo_parity_2026-09-20.md)
using a standalone reference cross-checked against the Python oracle and hand-derived
cases. Three complete primary chunk-size variants and scaling/dense controls matched
every audit field and checkpoint portfolio value. This covers synthetic v1;
changed native builds and representative production workloads require new evidence.

The [LAT-05 optimization evidence](../performance/fifo_optimization_2026-09-20.md)
records the current private traversal specialization, 30 full baseline/candidate
pairs, four control screens and renewed independent parity for the changed native
binary. Public APIs and execution semantics are unchanged; production and multi-session
performance qualification remain open.
