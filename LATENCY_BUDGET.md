# Native Replay Latency and Capacity Budget

Status: **v1 approved for experimental engineering acceptance; production qualification pending**.
Contract ID: `gambit-fifo-latency-v1`. Effective date: 2026-09-20.
LAT-09 review: **remain experimental; qualification not achieved**. See the
[2026-09-20 decision](documentation/performance/qualification_decision_2026-09-20.md).

## Approval and authority

The user instructed: “define and approve the workload and performance contract.”
This record exercises that delegated decision authority to approve the concrete
engineering scope below. Josh Myers remains the product/repository decision
authority. This is approval of the contract, not a claim that its targets have
been achieved, independently reviewed, or production-qualified. No additional
permission is needed to begin LAT-02 measurement within this scope.

LAT-01 is complete for this synthetic engineering workload. LAT-04 independently
verified its full-volume baseline traces on 2026-09-20; see the evidence below.
LAT-09 reviewed the accumulated evidence on 2026-09-20 and explicitly retained
experimental status. Production corpus selection, venue calibration, statistical
latency qualification and future promotion acceptance remain open under P1.2 /
Milestone 5. The review did not change this contract or claim its gates passed.
Changes to a workload, target or timer require a new version; never adjust a
threshold retrospectively to turn a failed result into a pass.

## Scope

- **Outcome:** reduce the time required to obtain an exact, auditable result for
  the fixed eight-instrument conservative FIFO historical replay.
- **Product boundary:** experimental `TopOfBookBacktester` with
  `execution_model="fifo"`; no general Python Strategy parity, live trading,
  exchange-calibrated execution, or supported production capability is inferred.
- **Reference:** [FIFO policy](documentation/architecture/fifo_queue_execution.md)
  and the [infrastructure plan](LATENCY_INFRASTRUCTURE_PROJECT_PLAN.md).
- **Owner:** Josh Myers owns contract and disposition decisions. Implementation
  and review roles follow LAT-02–LAT-09; no independent reviewer signoff is implied.
- **Correctness boundary:** zero dropped/reordered records. A missed target keeps
  the capability experimental. Never bypass validation, risk/cash admission,
  accounting, audit, integer overflow checks, or input ownership for speed.

### Approved workload v1

The canonical generator is `make_queue_events` in
[`benchmarks/top_of_book_backtest.py`](benchmarks/top_of_book_backtest.py), as
reviewed at Gambit `9c550bdae592e975f12046ebbb8d690889fd9206`. Baseline file SHA-256:
`e572922f98383d72a5dc7cb7c11938cf17cf0e0f9ee496c308aa1387f7a528ba`.
Harness edits may change that file digest but must retain identical generated
bytes for the canonical workloads; record the new digest with each artifact.

| Parameter | Approved value |
|---|---|
| Primary workload / ID | `fifo-3y-sparse-v1`; 946,944,000 records |
| Horizon meaning | Three-year volume equivalent including a leap day; synthetic zero-origin time, not actual historical dates |
| Instruments / ordering | IDs 0–7, round-robin; one shared portfolio; contiguous global sequence starting at zero |
| Record format | Aligned, contiguous, one-dimensional little-endian `QUEUE_DTYPE`, 88 bytes/record; trade followed by post-trade quote |
| Event rate / seed | 10 aggregate records per modeled second / `20260904`; run replay as fast as possible, without pacing sleeps |
| Prices / liquidity | Existing generator: integer bid levels stable for 256 instrument observations, two-tick spread, displayed sizes 50–150 lots, deterministic pseudo-random aggressor/trade size |
| Strategy | Alternate each instrument target between long 100 lots and flat every 10,000 observations of that instrument |
| Starting cash / fees | 10,000,000,000,000 common monetary units / 100 ppm rounded upward per fill |
| Units | Price tick 0.0001; lot size 0.001; common monetary unit 0.0000001 quote currency; no implicit FX |
| Order eligibility | Receive-time latency floor 1,000,000 ns; one active order per instrument, eight total |
| Feed age | Maximum receive minus event time 1,000,000,000 ns |
| Audit capacity | 1,000,000 orders, 1,000,000 corresponding queue rows, 1,000,000 fill rows; independent limits, no growth beyond them |
| Default chunk | 65,521 records; equivalence controls at 65,536 and 1,048,576; never exceed 1,048,576 |
| Terminal behavior | Preserve residual positions/open orders; mark longs at final bid; no invented liquidation or exit fees |

FIFO admission joins behind displayed same-side volume after the arrival event's
trade. Only later opposing exact-price trades consume volume ahead and then fill.
Quote decreases do not advance the queue. Rebalance cancellation is immediate
after that event's execution. Preserve global shared-cash priority and the
existing unsupported-arrival rejection policy. Shorts, leverage, funding, impact,
hidden liquidity, multiple own orders per instrument, arbitrary strategy callbacks,
external risk callbacks, venue cancel-ack latency and additional order types are
outside v1. Reject unsupported configuration instead of silently approximating it.

The primary input SHA-256 must be
`8e03b1e7d62ff6683a055718ef7818cf5188a56bf48084a1df1fb7095369465f`.
The baseline result SHA-256 is
`5e663f1117533205af50d1297faae98a791b720dff2215386fecb09272c8b2d6`.
Expected output: 88,776 orders; 595,838 fills; 82,864 filled orders; 5,912
arrival rejections; no terminal open orders; zero final positions; final
cash/equity 9,966,405,721,267; total fees 33,602,509,933. Hashes and counts are
regression controls, not independent proof of the execution model.

### Workload matrix

| ID | Configuration difference | Required use |
|---|---|---|
| `fifo-3y-sparse-v1` | Canonical parameters above | Primary execution and synthetic full-harness gates |
| `fifo-2y-sparse-v1` | 631,584,000 records | Volume-scaling control; 30 trials; correctness/resource gates and comparison, no separate absolute speed promise |
| `fifo-1m-dense-v1` | 1,000,000 records; rebalance every 16 instrument observations | Dense lifecycle stress; 30 trials; expected 55,365 orders, 95,485 fills, 40,713 cancellations, 3,502 arrival rejections, eight terminal open orders |
| `fifo-2m-dense-v1` | 2,000,000 records; rebalance every 16 | Twice the dense control's work, not a claim about live arrival rate; 30 trials, full trace/resource/progress checks; freeze input/output evidence in LAT-02 |
| `fifo-adversarial-v1` | Small fixtures: ties, gaps, duplicates, reversed receive time, stale events, malformed fields, overflow, exhausted audits, concurrent entry and aliased input ownership | Exact supported outcome; valid fixtures match oracle; invalid state never publishes success |
| `fifo-storage-v1` | Persist the canonical 88-byte input with immutable manifest; stream bounded chunks from local storage | Separate cold/warm load/decode/verify/persist wall measurements in LAT-02; no absolute storage-time promise in v1 |

Do not extend a dense scenario to three years with the current audit bound. Do
not use the 1.312-second market-only result as FIFO acceptance. Concurrent SPSC,
IPC, network and logger benchmarks require a separately scoped contract if
LAT-03 identifies a product need; they are not dependencies of this replay gate.

## Reference host and build

Approved engineering reference host `m4-local-v1`: this workstation's Apple M4,
10 logical CPUs, 24 GiB RAM (25,769,803,776 bytes), ARM64, macOS 15.5 build 24F74.
CPU/memory/OS were checked on 2026-09-20. Use CPython 3.10.20, NumPy 2.2.6,
Apple Clang 17.0.0 (`clang-1700.0.13.5`), and the existing setuptools C++11
`-O3` build. Preserve all actual compile/link arguments and dependency-lock hash;
no fast-math, host-specific SIMD, affinity tricks or sanitizer timing in the
acceptance build. Profiling symbols are allowed and must be recorded.

One replay process and one portfolio owner; no parallel replay trials. Use AC
power, disable low-power mode, prevent sleep, and close discretionary CPU/disk
workloads. Record power/thermal state, background activity and observed scheduling;
macOS default scheduling is accepted, not claimed pinned or isolated. Before each
measurement session require five minutes without discretionary heavy workloads.
Retain all runs. Only an objectively recorded predeclared environment failure
(sleep, power transition, thermal warning, competing scheduled workload, wrong
build/input) invalidates an entire affected session. Slow results alone do not.

Linux x86-64 and supported macOS/Python wheels still need correctness and build
coverage. The five-second threshold applies only to `m4-local-v1`; moving OS,
compiler, runtime or hardware requires a new baseline identity and contract review.

## End-to-end objective

The following are approved experimental engineering criteria, with attainment
**unmeasured for this contract**. They are not production promises.

| Boundary / scenario | Acceptance limit | Interpretation |
|---|---|---|
| Primary execution | p95 ≤ 5.000 s; observed maximum ≤ 7.500 s | Measured per-trial sum defined below, including initialization and materialization |
| Primary effective execution throughput | ≥ 189,388,800 records/s at the five-second boundary | Derived from fixed count/time, not another independent percentile |
| Primary synthetic full harness | p95 ≤ 75.000 s; observed maximum ≤ 90.000 s | Includes generation, hashes, ledger checks and output report; excludes one-time environment setup and the separate independent oracle campaign |
| Every valid workload | Peak replay-process RSS ≤ 512 MiB (536,870,912 bytes) | Whole fresh child process, including Python and result copies; oracle measured separately |
| Every valid workload | Zero loss/reordering; exact complete trace/accounting parity | A semantic mismatch blocks acceptance regardless of speed |
| Dense/control workloads | Report p50/p95/p99/max; no >5% unexplained p95 or RSS regression against the same-contract baseline | No extrapolated three-year dense target; follow hard resource and progress limits below |
| Storage-cold / storage-warm | Report all stages and total wall time; no >5% unexplained p95 regression versus same storage/input baseline | Absolute cold-load target deliberately excluded until LAT-02 evidence; no claim that the full 83.3 GB is resident |

The 75/90-second harness ceilings and 512 MiB resource ceiling are engineering
budgets chosen with headroom over the historical approximately 59-second / 262 MB
characterization, not newly measured performance. The 7.5-second maximum bounds
observed execution outliers; it is not a hard-real-time guarantee.

### Timing boundaries and statistics

- **Execution:** `initialization + sum(process_queue_batch duration) + result()`
  using `time.perf_counter_ns()` (or equivalent monotonic precision). Each batch
  timer includes Python/native call overhead, native validation, traversal,
  strategy, FIFO, risk/cash checks and accounting. Initialization starts before
  fresh engine construction; materialization ends after all immutable arrays
  and result scalars are available. Input generation/loading and hashing occur
  outside each batch timer. Name this **prepared-chunk execution sum**, never a
  continuous full-input end-to-end duration. Excluded intervals can affect cache
  and thermal state; record their order and durations consistently.
- **Synthetic full harness:** one continuous wall timer from immediately before
  fresh engine construction through generation, execution, input/result hashing,
  ledger reconciliation and writing/closing the result report. The supervisor
  records its own end timestamp after child exit. Report persistence separately;
  file close is not an fsync/durable-storage guarantee. Python process startup is
  reported by the supervisor separately, not included in the 75-second gate.
- **Storage path:** measure manifest verification, read/decode, processing,
  hashes, reconciliation and result persistence under a separate continuous wall
  timer. No full-dataset residency claim based solely on mmap. Label cold versus
  warm from recorded cache conditions; if coldness cannot be established, report
  it as uncontrolled rather than manufacturing a cold result.
- **Warm-up:** one untimed 1,000,000-event replay in a disposable fresh engine
  inside each trial process, then release it and construct fresh timed state.
  Warm-up may warm code/allocator pages but may not reuse portfolio state. Report
  its time; it remains outside execution and harness gates. Include its high-water
  memory in the process RSS bound.
- **Sampling:** screen with 30 paired alternating baseline/candidate trials.
  Qualification uses exactly 200 primary trials over four sessions of 50, with
  the same input/build/host contract; record every raw observation and session.
  Use nearest-rank empirical quantiles, maximum and jitter (`max - p50`).
- **p95 decision:** for each primary time ceiling, at most four of 200 trials
  may exceed its p95 limit, and every trial must satisfy its maximum limit.
  This is stricter than the empirical percentile alone and provides a one-sided
  binomial check of a 95% success rate at the 5% significance level under the
  independent-trial assumption. Report session correlation; if independence is
  not credible, call the result inconclusive. Do not add trials selectively until
  a failure turns into a pass; reruns are complete, separately retained campaigns.
- **Tails:** publish p50/p95/p99 with counts; p99 is descriptive with this sample
  size. p99.9 is **not qualified in v1**. Do not invent robust tail guarantees or
  add stage percentiles to obtain an end-to-end percentile.
- **Instrumentation:** batch probes only by default; measure probe overhead
  on/off. Target ≤1% execution overhead; if exceeded, use external sampling or
  report instrumented diagnosis separately from acceptance. Never subtract
  estimated instrumentation overhead from an observed acceptance time.

## Stage budget

These are diagnostic planning allowances totaling five seconds, not independent
percentile gates. Attribute the total before changing an implementation.

| Stage | Allowance | Allocation / blocking contract |
|---|---:|---|
| Initialization and input traversal | 0.75 s | Bounded reserve/setup; no I/O within batch processing |
| Strategy and risk/cash decisions | 1.50 s | Single owner; no per-event Python callbacks or unbounded work |
| Order lifecycle and FIFO simulation | 1.50 s | Pre-reserved audits; exact policy and checked capacities |
| Accounting and result construction | 0.75 s | Checked integer arithmetic; bounded final array allocation/copies |
| Headroom | 0.50 s | Variance only, never omitted work |

## Time and ordering

Source `event_time_ns`, `receive_time_ns`, receive-time decisions and measured
monotonic durations are distinct clocks. Host wall time supplies metadata only.
The canonical generator uses `event_time_ns = sequence * 100,000,000` and
`receive_time_ns = event_time_ns + 1,000,000`. Global sequence is contiguous from
zero and receive time is nondecreasing. Equal receive times retain sequence order.
No live transmit timestamp exists. Gaps, duplicates, invalid source/receive
relationships, stale values or reordering fail admission; preprocessing may not
silently sort, coalesce or discard events. Host wall-clock steps cannot alter
replay decisions. Production clock-quality policy is outside synthetic v1.

## Resource bounds

- Eight instrument states, one active order per instrument, one native owner.
  Reentrant/concurrent batch or result entry is explicitly rejected.
- At most one chunk submitted at a time; chunk ≤1,048,576 records (92,274,688
  bytes). Input buffers remain owned and immutable, including all aliases, until
  processing returns. No background mutation while the GIL is released.
- Each order/fill/queue audit has the approved 1,000,000-row bound. No lossy audit
  export or sampling. Allocation failure, integer overflow or audit exhaustion
  invalidates the run permanently; no partial result may be published.
- Runner admission must check chunks, scenario configuration and resource limits;
  the native API alone is not claimed to enforce every v1 harness bound.
  LAT-02 now implements the controlled runner admission/watchdogs; see the
  [runbook](documentation/operations/controlled_replay.md). Independent evidence
  and the full qualification campaign remain required.
- Cold/warm storage reads are bounded by chunks; track faults, bytes, RSS,
  allocation/copy counts, CPU and context switches. Disk mappings do not remove
  memory pressure. Canonical logical input is 83,331,072,000 bytes.
- No event dropping, silent growth, or unbounded retries. The direct replay has
  no transport queue. SPSC full/age/wakeup policy belongs to a future handoff
  contract, not this five-second replay budget.
- Supervisor limits: 10 seconds for setup/warm-up, 5 seconds per native batch
  call, 120 seconds per primary/control/dense timed harness, 30 seconds without
  progress and 600 seconds total for a storage trial. On timeout request stop,
  wait at most one second, then terminate the isolated benchmark worker and mark
  failure. Final process exit/cleanup must occur within two additional seconds;
  exceeding it fails the lifecycle gate. These are harness limits to implement
  and test, not existing hard-real-time native guarantees.
- Cooperative cancellation is checked between chunks; stop without submitting
  another chunk. Publish only a failed/cancelled diagnostic artifact, never an
  ordinary successful replay result. No asynchronous mutation of active engine
  state. A stuck native call is handled by the worker watchdog above.
- Invalid input/capacity must raise by the return of the batch containing the
  offending record, within the batch watchdog. For valid completion, final
  materialization is inside the execution gate; shutdown/diagnostic cleanup is
  subject to the lifecycle bound. Test allocation/disk failures explicitly.

## Evidence

| Scenario | Preserved artifact | Current decision |
|---|---|---|
| FIFO baseline | [2026-09-04 FIFO report](documentation/performance/fifo_backtest_2026-09-04.md), adjacent `fifo_backtest_2026-09-04_3y_trial1.json`, `trial2.json`, `trial3.json` | 9.084–9.150 s, median 9.105 s; misses the approved five-second engineering objective |
| Market-model control | [Top-of-book report](documentation/performance/top_of_book_backtest_2026-09-04.md) | Different model, not FIFO qualification |
| Factor-only control | [Crypto tick parity](documentation/performance/crypto_tick_parity_2026-09-04.md) | Not an order-to-P&L workload |
| Dense control | `documentation/performance/fifo_backtest_2026-09-04_dense.json` | Existing one-million-event counts and hashes; not a controlled distribution |
| v1 campaign | No new 200-trial campaign run | Not accepted yet |
| Independent oracle / LAT-04 | [Full-volume evidence](documentation/performance/fifo_parity_2026-09-20.md): full-volume independent trace parity complete for v1; three primary chunk sizes, 2y scaling and 1m/2m dense controls; every audit field and 3,230 portfolio checkpoints matched | Baseline synthetic correctness gate satisfied; repeat for changed native builds/workloads; production corpus and performance qualification remain open |
| LAT-02 runner | [2026-09-20 evidence](documentation/performance/controlled_replay_2026-09-20.md): full primary and 2m-dense characterization; bounded storage and fault smoke tests | Runner verified locally; 200-trial campaign and full-size storage remain open; baseline independent oracle completed separately in LAT-04 |
| LAT-03 diagnosis | [2026-09-20 profiles and overhead](documentation/performance/fifo_profile_2026-09-20.md): separate native FIFO/ring profiles, full-volume Python attribution and 30 paired comparisons per sparse-prefix/dense workload | Diagnosis complete; sparse execution probe effect +0.42% with 95% interval −0.90% to +1.17%; primary 1% overhead target inconclusive, no performance qualification |
| LAT-05 optimization | [Measured FIFO specialization](documentation/performance/fifo_optimization_2026-09-20.md): 30 full primary pairs, p50 9.663 → 4.138 s, 57.19% median paired reduction (95% interval 57.06–57.38%); four control screens and renewed six-case parity passed | Retain as experimental; isolated dense maximum-RSS outlier investigated with retained replication/allocation evidence; probe-overhead, multi-session and production qualification remain open |
| LAT-09 disposition | [Qualification review](documentation/performance/qualification_decision_2026-09-20.md): preserved trace evidence rechecked, fresh source-matched correctness/sanitizer/static-analysis/macOS package checks, eight controlled development trials | Remain experimental; primary 3.982–4.068 s is unqualified. Four-session/200-trial, full-volume probe, Linux x86-64, committed-candidate and representative production/reviewer requirements remain open |

The [2026-09-21 LAT-09 follow-up](documentation/performance/qualification_followup_2026-09-21.md)
adds committed candidate `126451b`, successful supported-platform release CI
and 30 full-volume probe pairs. Median execution overhead is +0.719%, with a
95% interval of +0.407% to +1.025%: the ≤1% target remains inconclusive and has
not been relaxed or rounded into a pass. The 200-trial campaign remains unstarted;
production-data validation is deferred and Joshua Myers's review is pending.

The three historical runs do not establish p95, p99 or worst-case behavior.
Historical artifacts retain their original candidate/unapproved language as
provenance; this version supersedes their proposed engineering contract only.

### Required acceptance evidence

1. LAT-02 runner controls and reproducible scenario manifests, raw samples,
   all timing boundaries and resource observations on the approved reference host.
2. Independent full-volume comparison of every supported order, fill, queue audit
   and portfolio value; hashes/terminal reconciliation alone are insufficient.
   Stream the oracle outside timed runs with bounded memory; budget at most 24
   CPU-hours and 4 GiB RSS for an initial attempt. If exceeded, mark evidence
   incomplete and review the oracle plan; never waive parity or claim success.
3. Same-commit malformed/boundary tests, ASan/UBSan, applicable TSan, native
   warnings/static analysis and supported-platform package checks.
4. Host, power/thermal/session state, source/binary/lock/input/configuration hashes,
   exact compiler/link flags, raw output and result identities, and validation
   status in every evidence bundle. Retain failed sessions and predecessor builds.
5. An explicit promote, retarget, or remain-experimental decision. Passing v1
   permits an engineering performance claim only for this synthetic contract.
   Production promotion additionally needs representative owned data/strategy,
   calibrated assumptions and all existing P1.2/Milestone 5 acceptance gates.

Shared CI executes correctness and report-schema smoke checks. Time qualification
belongs to the controlled reference-host sessions, never a noisy shared runner.

## Change control

- Profile the approved workload before changing validation, dispatch or layout.
  Prefer the smallest change preserving all causal/numerical/audit invariants.
- Retain a candidate when it improves the affected end-to-end path by ≥10% with
  a 95% paired confidence interval excluding no improvement, or has an explicitly
  recorded capacity/tail benefit accepted by the owner. No unexplained >5%
  regression in control-workload p95 or peak RSS; ties/noisy results are inconclusive.
- Stop after three variants or five engineering days per unresolved hypothesis;
  re-profile or revise the work proposal. These limits do not establish success.
- Revert/disable an optimization on a correctness, capacity, tail, portability or
  lifecycle failure. Preserve baseline source/build and same-workload evidence;
  never reuse a failed engine's partial result. Keep native replay optional.
- Contract approval: delegated by the user's explicit instruction on 2026-09-20;
  engineering decisions recorded here by Codex. Independent review and achieved
  acceptance results are not represented as approved.
