# Native Replay Latency and Capacity Budget

Status: **candidate; not approved for production acceptance**.

This record applies to the experimental native top-of-book and conservative
FIFO historical replay paths. It follows the production-project-template
latency-budget structure. The values below separate measured characterization
from proposed acceptance criteria; a proposal is not a production performance
promise.

## Scope

- **Path and business outcome:** replay a fixed, compiled, eight-instrument
  characterization strategy over two to three years of preprocessed event data,
  including orders, fills, fees, cash, positions, P&L, and immutable audit
  results. This does not cover the general Python `Strategy` API or live trading.
- **Start event / end event:** candidate execution timing starts immediately
  before construction of a fresh backtester with validated input available and
  ends after final result arrays are materialized. It includes initialization,
  native batch processing, and result construction. Current characterization
  sums those stages but excludes input generation/loading, hashing, caller-loop
  overhead, and independent ledger verification; that limitation must remain
  attached to the results until an owner approves the final boundary.
- **Correctness boundary if a deadline is missed:** retain exact replay and fail
  the performance decision. Never drop, reorder, approximate, or skip events,
  orders, fills, risk checks, accounting, or audit records to meet a deadline.
  A missed target keeps the capability experimental.
- **Owner and review date:** decision authority Josh Myers; representative
  workload, reference host, final threshold, and review date are pending.
- **Production hardware, OS, runtime/compiler, and topology:** not approved.
  Existing characterization used an Apple M4 with 10 logical CPUs and 24 GiB
  RAM, macOS 15.5 ARM64, Apple Clang 17.0.0, C++11 `-O3`, CPython 3.10.20, and
  NumPy 2.2.6, without affinity, isolation, fast-math, or concurrent portfolio
  processing.
- **Workload/data-set version and traffic mix:** candidate volume is
  946,944,000 synthetic trade-plus-quote records, eight round-robin instruments,
  10 aggregate records/second, seed `20260904`, and conservative FIFO execution.
  This is volume equivalence, not exchange-calibrated or approved production
  data. Strategy and order/fill frequency approval remain pending.
- **Measurement clock and instrumentation overhead:** Python
  `time.perf_counter()` measures monotonic durations. Input SHA-256, result
  SHA-256, ledger reconciliation, progress reporting, and resource observation
  are evidence work outside the current execution sum and must be reported
  separately.

## End-to-end objective

All thresholds in this table are **proposed and unapproved**. `TBD` is an open
decision, not permission to omit a measurement. Percentiles require enough
independent repetitions on the approved controlled host; three historical runs
do not establish p95, p99, or worst-case behavior.

| Load | p50 | p95 | p99 | p99.9 | Maximum/jitter | Throughput | Error/loss limit |
|---|---:|---:|---:|---:|---:|---:|---:|
| Expected: three-year candidate | TBD | proposed ≤ 5.000 s | TBD | TBD | TBD | proposed ≥ 189,388,800 records/s | zero dropped/reordered records; exact trace/accounting parity |
| Peak | TBD | TBD | TBD | TBD | TBD | TBD | zero silent loss; explicit bounded rejection only |
| Overload | N/A | N/A | N/A | N/A | bounded failure time TBD | no throughput promise | fail explicitly without a publishable partial result |

The conservative FIFO characterization currently measures 9.084–9.150 seconds
for three years, with a 9.105-second median across three trials. That misses the
proposed five-second objective and is too small a sample for an empirical p95.

## Stage budget

These planning allowances partition the proposed five-second objective. They are
not independently measured percentile claims and may not be added to manufacture
an end-to-end percentile.

| Stage | Owner | p99 budget | Allocation/blocking allowed? | Queue capacity / max age | Failure or degradation behavior |
|---|---|---:|---|---|---|
| Initialization and input traversal | Native/performance owner | candidate 0.75 s | bounded initialization allocation; no live I/O in accepted execution boundary | chunk ≤ 1,048,576 records; feed age ≤ 1 s | reject invalid layout, sequence, staleness, or capacity |
| Strategy and portfolio-risk decisions | Native/performance and risk owners | candidate 1.50 s | no per-event Python callback or unbounded allocation | one active order per instrument in current prototype | reject unsupported behavior; never bypass cash/risk policy |
| Order lifecycle and fill simulation | Native/performance and execution owners | candidate 1.50 s | bounded preallocated audit only | audit capacity must be approved; current characterization uses 1,000,000 | invalidate run on overflow/exhaustion; publish no result |
| Accounting and result construction | Native/performance and accounting owners | candidate 0.75 s | final bounded result allocation allowed | exact integer range and configured audit bounds | raise on overflow or allocation failure; publish no partial result |
| Headroom | Native/performance owner | candidate 0.50 s | not a work stage | N/A | absorbs measured variance only; cannot hide omitted work |

## Time and ordering

- **Wall-clock source and synchronization:** no wall clock participates in
  replay decisions. Benchmark wall metadata comes from the host and is not an
  event-time authority.
- **Monotonic duration clock:** `time.perf_counter()` in the Python harness.
  Native stage probes must use an equivalent monotonic source and document
  conversion and resolution.
- **Source, receive, decision, and transmit timestamp definitions:** each input
  carries event and receive nanoseconds. Strategy decisions occur while
  processing the current receive-ordered record. Orders become eligible only on
  a later instrument event meeting configured receive-time latency. There is no
  live transmit timestamp in historical replay.
- **Sequence/gap/duplicate/reordering policy:** the prototype requires a global
  contiguous sequence starting at zero and nondecreasing receive time. Gaps,
  duplicates, reordering, stale data, and malformed values fail the run; a
  preprocessing stage may not silently sort or discard them.
- **Clock-step, drift, and synchronization-loss behavior:** host wall-clock steps
  do not change replay semantics. Invalid event/receive relationships and stale
  events fail validation. Production preprocessing clock-quality policy remains
  an approval prerequisite.
- **Replay inputs and configuration identity:** retain input, result, native
  extension, C++ source, benchmark, configuration, seed, and chunk-size identity
  with every accepted result.

## Resource bounds

- **Maximum in-flight work:** current prototype supports eight configured
  instruments in the candidate workload, one active order per instrument, a
  chunk limit of 1,048,576 records, and explicitly configured audit capacity.
  The accepted workload must set maximum orders, fills, queue records, and
  cancellation work from measured production-like data.
- **Memory/allocator/page-fault assumptions:** current chunk-resident FIFO trials
  peaked near 262 MB process RSS, but their logical 83.3 GB input was generated
  incrementally. Accepted cold and warm measurements must distinguish resident
  pages, storage reads, faults, allocator activity, audit growth, and final
  result copies.
- **Thread ownership, affinity, NUMA, and isolation assumptions:** replay state
  has one mutable owner and rejects concurrent batch entry. Current measurements
  use no affinity or isolation. The reference-host topology and interference
  controls are pending.
- **External dependency deadlines:** none inside the proposed prepared-input
  execution boundary. Loading, decompression, preprocessing, and persistence
  need separate measured stages and explicit failure behavior.
- **Backpressure, rejection, shedding, and stale-data policy:** bounded capacity
  or stale/invalid input invalidates the run. No event shedding, sampling,
  lossy coalescing, or partial-result publication is allowed.
- **Startup, warm-up, shutdown, and drain limits:** pending approval. Each timed
  trial must start from fresh portfolio state. Warm-up may establish code/data
  residency but may not reuse terminal strategy state; cold load, warm-up,
  execution, materialization, verification, and shutdown are reported separately.

## Evidence

| Scenario | Command / artifact | Result distribution | Decision |
|---|---|---|---|
| FIFO baseline | [`documentation/performance/fifo_backtest_2026-09-04.md`](documentation/performance/fifo_backtest_2026-09-04.md) and adjacent raw JSON | three-year execution 9.084, 9.105, 9.150 s; median 9.105 s | misses proposed 5 s; remain experimental |
| Market-model characterization | [`documentation/performance/top_of_book_backtest_2026-09-04.md`](documentation/performance/top_of_book_backtest_2026-09-04.md) and adjacent raw JSON | final three-year single run 1.312 s; earlier build 1.693–1.699 s | different execution model; not FIFO acceptance evidence |
| Factor-only replay/determinism | [`documentation/performance/crypto_tick_parity_2026-09-04.md`](documentation/performance/crypto_tick_parity_2026-09-04.md) and raw JSON | one full replay; C++ processing 37.09 s with Python parity | correctness characterization only; not order-to-P&L acceptance |
| Peak | No approved artifact | TBD | open |
| Overload | Capacity/overflow unit and sanitizer tests; production-like workload absent | no controlled latency distribution | correctness guards exist; performance decision open |
| Dependency failure | No live dependency in execution boundary | N/A | loading/preprocessing failure budget open |
| Full replay/determinism | FIFO hashes and full fill-ledger reconciliation; full-volume independent trace parity absent | three native result hashes identical | necessary evidence, not sufficient for promotion |

### Required acceptance evidence

Before promotion, the owner must approve the strategy, event/order/fill mix,
real or validated preprocessed corpus, host and isolation policy, compiler/build,
repetition and warm-up counts, execution semantics, capacities, overload policy,
and final timer boundary. Evidence must then include:

1. cold load, preprocessing, warm-up, initialization, execution, materialization,
   verification, persistence, and end-to-end distributions;
2. p50, p95, p99, p99.9, maximum, jitter, CPU time, throughput, and peak resource
   measurements at expected, peak, and overload volumes;
3. complete order/fill/queue/accounting parity against an independent reference
   for the accepted workload;
4. exact input/configuration/build identities plus sanitizer, compiler-warning,
   static-analysis, and deterministic-replay results; and
5. an explicit promote, retarget, or remain-experimental decision.

Shared CI may execute benchmark correctness smoke tests, but timing thresholds
must not be enforced on noisy shared runners.

## Change control

- **Performance hypothesis:** pending a profile of the approved FIFO workload;
  current guidance is to inspect validation, dispatch, and data movement first.
- **Correctness and risk controls that may not change:** causal visibility,
  sequence/order rules, queue model, cash/risk admission, fees, overflow checks,
  exact ledger reconciliation, capacity failures, and immutable complete results.
- **Before/after evidence:** preserve raw distributions, workload and build
  identity, resource measurements, correctness parity, sanitizer/static-analysis
  results, and the preceding baseline for every optimization.
- **Complexity introduced:** record new allocation, concurrency, platform,
  maintenance, and fallback costs in the optimization change.
- **Rollback trigger and procedure:** revert or disable an optimization if it
  changes trace/accounting results, violates a bound, worsens accepted tail or
  resources, or loses supported portability. The whole native replay remains
  optional and can be withdrawn without changing the general Strategy API.
- **Approver and date:** pending native/performance-owner approval.
