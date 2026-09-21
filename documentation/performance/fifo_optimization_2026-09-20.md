# LAT-05 FIFO traversal specialization

Date: 2026-09-20. Contract: [`gambit-fifo-latency-v1`](../../LATENCY_BUDGET.md).
Decision: **retain the optimization as experimental; LAT-05 complete**. One source
variant passed the primary and four control screens, renewed independent full-volume
parity, and native validation. Qualification and production promotion remain open.

The full primary screen found a **57.19% median paired execution reduction**
(95% interval **57.06–57.38%**): p50 **9.663 → 4.138 seconds** across 30 complete
baseline/candidate pairs. Full-harness p50 fell **62.357 → 56.783 seconds**.
These are development observations, not an approved five-second p95 guarantee.

## Change and mechanism

The [native change](../../src/gambit/cpp/factor_cache/top_of_book_backtest.cpp)
addresses the first [LAT-03 hypothesis](fifo_profile_2026-09-20.md): repeated
per-event calls/state accesses. The private processing function now has a compile-time
execution-model parameter; its infrequent rebalance body is a separate helper with
the same parameter. Existing batch entry points select the specialization after their
mode checks. Clang folds processing into each batch loop: the separately called
`process` symbol observed in LAT-03 is absent in the retained candidate disassembly.
No compiler-specific inlining attribute, new dependency or C++ standard change is used.

Queue validation still precedes book validation on each original event boundary.
Pending orders are processed before rebalance; receive-time and processed state still
commit per event. Capacity checks, checked integer arithmetic, audit layouts, mutex,
GIL release and exception poisoning remain intact. Six new
[failure-order tests](../../tests/test_fifo_backtest.py) cover three chunk sizes,
including capacity failure before malformed later input and trade-before-book
validation on a multiply invalid event. Existing market-mode parity tests also pass.

## Method and primary distributions

Only one source variant was tried. Three preliminary alternating 100-million-event
prefix pairs justified further work; they are retained separately and excluded from
the final comparison. The final primary screen replays **946,944,000 events per
trial**, in fresh processes after a disposable one-million-event warm-up, using the
unchanged [LAT-02 runner](../operations/controlled_replay.md), generator, hash work,
probes and timer boundaries. Baseline and candidate order alternates between pairs.
The baseline uses a preserved source/binary snapshot and Python wrapper; both variants
share the same dependency environment. Other native extensions are identical during
the final screening. Builds and tests finished before the timing campaign.

Host: Apple M4, 10 logical CPUs, 24 GiB, macOS 15.5 build 24F74, Python 3.10.20,
NumPy 2.2.6. All 300 original screening workers matched the reference host and had
stable per-variant identities. AC power and low-power mode off were observed; power
reports recorded no thermal/performance warning. These are not temperature/frequency
traces or an attested quiet/independent session. `caffeinate -i` prevented idle sleep.
The primary campaign ran 20:59:40–21:59:30 UTC; the four controls followed serially.

Each variant has 30 observations. Quantiles use nearest rank; p99 equals the maximum
at this count and is descriptive. Jitter is maximum minus p50. Intervals use the
runner's fixed 10,000 paired bootstrap resamples of median relative execution
reduction, seed 20260920. No observation is discarded or adjusted for estimated probe cost.

| Metric / variant | p50 | p95 | p99 | Maximum | Jitter |
|---|---:|---:|---:|---:|---:|
| Execution (s) / baseline | 9.663 | 9.717 | 9.752 | 9.752 | 0.089 |
| Execution (s) / candidate | 4.138 | 4.175 | 4.179 | 4.179 | 0.042 |
| Harness (s) / baseline | 62.357 | 62.580 | 62.797 | 62.797 | 0.440 |
| Harness (s) / candidate | 56.783 | 57.097 | 57.122 | 57.122 | 0.339 |
| Process RSS (MiB) / baseline | 210.969 | 222.125 | 222.594 | 222.594 | 11.625 |
| Process RSS (MiB) / candidate | 203.375 | 224.609 | 225.516 | 225.516 | 22.141 |

Primary maximum RSS rose **1.31%**, remaining below 512 MiB. Initialization and result
copies remain inside execution; generation, input/result hashing, ledger checks and
report completion remain inside the full harness. The harness p50 reduction is 8.94%;
the declared affected path for LAT-05 is prepared-chunk execution. No input hashing
or verification was removed to improve timings.

## Controls and memory investigation

Both dense workloads are complete. The chunk-size timing controls each use the first
100 million canonical events, with 30 alternating pairs; their tails are diagnostic.
Independent parity separately covers all 946,944,000 events at both chunk sizes.
Negative deltas below mean reductions. All five original runner comparisons returned
`retention_signal`; all p95 comparisons stayed within the 5% regression threshold.

| Control | Median execution reduction (95% interval) | Execution p95 Δ | Harness p95 Δ | RSS p95 Δ | Maximum RSS Δ |
|---|---:|---:|---:|---:|---:|
| 1m-dense | 27.29% (26.80–27.79%) | -16.10% | +3.89% | -1.96% | +0.01% |
| 2m-dense | 28.30% (26.88–29.04%) | -33.89% | -4.92% | +1.16% | +6.99% |
| chunk-65536-prefix | 57.40% (56.70–57.85%) | -57.14% | -9.70% | +0.20% | -8.25% |
| chunk-1048576-prefix | 57.01% (56.85–57.37%) | -56.84% | -6.38% | +1.56% | +1.75% |

The separate maximum check found **2m-dense candidate trial 008 at 147.375 MiB**,
versus the baseline maximum **137.750 MiB**: **+6.99%**, despite a +1.16% RSS p95
delta. This observation is retained and explicitly investigated, not omitted from
the comparison. Largest-chunk maximum RSS was 471.969 MiB, below the 512 MiB bound.

A [predeclared follow-up](fifo_optimization_2026-09-20/memory-followup-plan.json)
ran one complete new 30-pair normal replication and 30 separate diagnostic pairs.
The normal replication retained the same code, workload and probes and passed the
runner screen: baseline/candidate maximum RSS was **143.188 / 127.766 MiB**;
RSS p95 delta was **+1.05%**. Its host CPU metadata was sandbox-limited, and it is
used to investigate memory, not added to the original performance distribution.

The diagnostics recorded current RSS, high-water RSS and all-zone macOS malloc
statistics at existing runner phases. They are explicitly unusable for timing
acceptance. All 60 diagnostic runs had the same **20,386,344 output-array bytes**;
every result copy increased live malloc bytes by exactly **20,398,080 bytes**.
The median live-allocation increase through replay was exactly **145,509,952 bytes**
for each variant. The corresponding p95 increases were **151,278,656 / 151,196,736**
bytes, with no candidate increase. RSS variation was already present after discarded
warm-up and before the first timed batch: baseline range **90.000–119.719 MiB**,
candidate **89.797–105.594 MiB**. Diagnostic maximum RSS was
**149.922 / 137.688 MiB**, again larger for the baseline.

**Memory decision:** evidence supports fresh-process allocation/residency variability
as the explanation for the isolated original maximum. No persistent allocation
increase was found; native capacities and allocation sites are unchanged. Retain the
candidate and the outlier with this explanation. The original run did not have stage
allocation probes, so exact per-allocation attribution is not claimed. Keep the
512 MiB watchdog and the future qualification/resource review; do not pool the
follow-up into the original screen or present it as an independent qualification session.

## Independent correctness and validation

The unchanged [LAT-04 standalone reference](fifo_parity_2026-09-20.md) compared this
exact candidate binary across all six complete cases: three primary chunk sizes,
the 631,584,000-event scaling case and both dense cases. All **3,475,416,000 events**,
**3,230 checkpoints**, and every order/fill/queue field and portfolio value matched.
Primary outputs retain the canonical 88,776 orders, 595,838 fills, final zero positions,
and cash/equity 9,966,405,721,267. Input and result hashes matched the v1 controls.
The campaign used 364.191 CPU seconds and 345.843 wall seconds; maximum combined
oracle/worker high-water RSS was 1.900 GiB within the separate 4 GiB correctness budget.
These correctness timings and memory are not replay performance measurements.

Validation: **2,199 tests passed**; **141 focused tests passed under ASan/UBSan**,
including supervised dense parity; 10/10 financial mutations killed; lint, mypy
(58 files), C++11 native warning gates, documentation, wheel/sdist, Twine and artifact
inventory passed. Local sanitizers use macOS with leak detection disabled; Linux
LSan and supported-platform CI remain qualification work. No concurrent state or
synchronization was added. Final documentation/contract checks are recorded separately.

Retained failed attempts cover missing local build tools, exit 137 during Mach-O
replacement, sanitizer-child runtime inheritance and sandbox build-dependency DNS.
Their retries passed after cached build-tool installation, atomic binary replacement,
re-exporting the injected sanitizer runtime before child creation, and permitted
dependency access. No production-code exception was introduced for these environment issues.

## Provenance, reproduction and rollback

Baseline native SHA-256:
`593537a814f3364f384df5cb1a33df86aa8b8870edd88c020756026111d5b7d2`.
Candidate native SHA-256:
`9bb6074a0149a302edd4a1f8ab3063f3ae830b15418342738dcacfde450ffb39`.
Candidate source SHA-256:
`f105edf624b52419ce35d1951f3554842b9f0168eb83cbb0c857812f1eeaca37`.
Checkout revision is `9c550bdae592e975f12046ebbb8d690889fd9206` plus retained dirty
source snapshots. Candidate commands reproduce setup.py's factor-extension build
with Apple Clang 17.0.0 (`clang-1700.0.13.5`), C++11 and `-O3`, without fast-math or
new CPU targeting. Baseline historical exact compile/link attestation remains absent.

- [Primary raw distributions](fifo_optimization_2026-09-20/3y-sparse-summary.json),
  [all comparisons](fifo_optimization_2026-09-20/comparisons.json), and
  [fixed screening commands](fifo_optimization_2026-09-20/screening-plan.json).
- [Candidate build commands](fifo_optimization_2026-09-20/candidate-build.json) and
  [sanitized build](fifo_optimization_2026-09-20/sanitized-build.json).
- [Independent parity summary](fifo_optimization_2026-09-20/parity-summary.json) and
  [memory follow-up data](fifo_optimization_2026-09-20/memory-followup-summary.json).
- [Manifest, archive hashes and complete external artifact locations](fifo_optimization_2026-09-20/manifest.json).
  The sibling raw archives preserve every trial, including diagnostics and retries;
  the validation/reproduction archive preserves scripts, source, logs and disassembly.

Full binaries, NPZ traces and source snapshots are retained outside Git at
`/Users/jkm0607/Projects/gambit-evidence/lat05-2026-09-20`. Reproduce using the retained
commands with equivalent preserved baseline/candidate environments and fresh output
directories; run trials serially. Rebuilds have new identities and require new parity.
To roll back, restore only the native implementation from `baseline/src/gambit/cpp/
factor_cache/top_of_book_backtest.cpp` in that evidence directory and rebuild.
Keep the tests and unrelated LAT work. No public API or data migration is required.

This development screen does not resolve the LAT-03 primary probe-overhead target,
replace four independent sessions of 50 qualification trials, establish full-volume
chunk-control tails, or approve a production corpus/strategy. Those remain LAT-09
and production-readiness gates. LAT-06 requires an actual concurrent workload;
LAT-07 can address the separately measured generation/load costs.
