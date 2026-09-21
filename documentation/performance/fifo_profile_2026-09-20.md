# LAT-03 FIFO profile and instrumentation overhead

Date: 2026-09-20. Contract: [`gambit-fifo-latency-v1`](../../LATENCY_BUDGET.md).
Status: diagnosis complete; native replay remains experimental. No native
optimization or performance qualification is claimed.

## Method and evidence

[`profile_replay.py`](../../benchmarks/profile_replay.py) adds bounded diagnostic
sampling, Python attribution and alternating paired probe comparisons to the
[LAT-02 runner](../operations/controlled_replay.md). Every diagnostic trial is
explicitly ineligible for qualification, even if its timings meet a budget.
The existing native extension was used without rebuilding or inserting per-event
probes. Separate processes exercised direct FIFO execution and concurrent native
factor handoff; ring throughput is not FIFO execution latency.

The host was Apple M4, 10 logical CPUs, 24 GiB RAM, macOS 15.5 build 24F74,
Python 3.10.20 and NumPy 2.2.6, on AC with low-power mode off. Power observations
reported no recorded thermal/performance warnings; they are not a temperature or
frequency trace. These are development sessions without an attested quiet period,
CPU affinity/isolation or independent session review. Exact source, runner, binary,
input and output hashes and observed load are retained in the artifacts. Checkout
revision: `9c550bdae592e975f12046ebbb8d690889fd9206` plus the recorded dirty state.
The extension SHA-256 is
`593537a814f3364f384df5cb1a33df86aa8b8870edd88c020756026111d5b7d2`.
Native compile/link-command attestation remains absent.

macOS `/usr/bin/sample` captured 20 seconds at a requested 1 ms interval. Sampling
required permission outside the filesystem/process sandbox. Xcode Instruments was
unavailable. Samples include blocked threads, and native inlining/tail calls limit
stack attribution: counts below are observations of instruction pointers, not CPU
percentages, cycle counts, branch-miss rates or proof of a particular cause.
Profiler timings must not serve as an acceptance baseline.

## FIFO findings

The sampled run completed all **946,944,000 events**, producing the canonical
**88,776 orders and 595,838 fills**. Input and result hashes matched the approved
controls, and the ledger reconciled. This is regression evidence, not an
independent full-volume oracle.

| Stage | Sampled full-volume time |
|---|---:|
| Synthetic generation | 26.417665 s |
| Input SHA-256 | 25.617887 s |
| Prepared-chunk execution sum | 9.505865 s |
| Result materialization, included in execution | 0.006916 s |
| Ledger reconciliation | 1.425375 s |
| Result hashing | 0.012536 s |
| Supervisor full harness | 63.836204 s |
| Peak process RSS | 223,625,216 bytes (213.27 MiB) |

The FIFO worker's main thread had 17,014 stack samples. The separate Polars cleanup
thread had another 17,014 sleeping samples; exclude it from the main-thread
denominator. The collapsed leaf table reports 6,847 SHA-256 samples (40.2% of
main-thread observations), 1,651 in `TopOfBookBacktester::process`, and 800 in
`process_queue_batch`. The call graph contains only two leaf observations in
`fill`, below the collapsed table's five-sample display threshold. Sparse fill
accounting and final array copying are weak first optimization candidates.

Two dominant `process` instruction locations were its initial processed-counter
load (+36) and final receive-time store (+1248). The hot batch location (+184)
was an aggressor validity comparison. Disassembly supports investigating repeated
call/state access and validation layout, but sampling skid and absent hardware
counters prevent a claim that those individual instructions caused the cost.
All validation must remain enforced on the original event boundary.

Generation and input hashing together account for roughly 81.5% of the sampled
harness. A 10% native execution improvement would save only about 1.5% of this
harness, while reaching five seconds from this observation requires roughly 47%
less native execution time. These are arithmetic illustrations, not predicted
speedups. Input hashing remains inside the declared harness; removing it or moving
it outside the timer would change the contract. Storage performance was not
profiled and cannot be inferred from synthetic generation.

A separate full-volume cProfile run also matched both canonical hashes. It
attributed 25.143836 s to hash updates and 24.695184 s cumulatively to
`make_queue_events` (14,453 chunks), including 2.916867 s in `numpy.zeros`.
Ledger reconciliation took 1.582935 s and 28,909 protocol emits took 0.169475 s
cumulatively. These nested costs must not be added twice. The profiler's summed
entry time of 51.623966 s omits work in the already-running worker frame and
unattributed native calls; it is not a full-harness timer. The independent stage
clocks measured 9.303201 s of native execution. Use cProfile for Python attribution
and the native sample for C++ attribution, not as interchangeable timing gates.

## Concurrent handoff findings

The separate native in-place factor path used one million prebuilt, read-only
records per iteration, one instrument, batch size 1,024, capacity 65,536, spin count
256 and a 10 ms park timeout. Each iteration created a fresh ring and processor.
It completed 816 iterations (816 million records) in 23.005 seconds, including the
sampling window, with zero sequence errors and rejected pushes. Those iterations
are not independent acceptance trials and include repeated thread lifecycle cost.

Across all ring threads, the sampler observed 12,747 leaf stacks in
`TickRing::push_batch`, 2,566 in `TickFactorProcessor::process`, and 1,448 in the
factor map insertion/lookup helper. It also observed 24,115 condition waits and
16,965 waits in the idle Polars cleanup thread. Wait samples describe blocking;
they must not be added to active costs and called CPU utilization. The ring metrics
recorded 272,863 parks/wakeups, 1,270,996 yields and zero park timeouts. The producer
still performs close checks, cursor publication and a diagnostic atomic increment
per record inside its batch. Factor hash-map work is a separate limit on any
transport improvement.

## Instrumentation overhead

Two modes replay identical immutable inputs with identical native validation,
result hashes and ledger reconciliation. `full` uses normal stage clocks,
per-batch RSS checkpoints and batch/progress messages. `timers_only` retains the
two native-call clock reads, initial/final measurements, parent RSS polling,
cancellation, overall/progress watchdogs and approximately one-second progress
heartbeats. It omits optional stage timing and per-batch telemetry/RSS checks;
unmeasured stage times are null. It is a diagnostic control, not a selectable
production runner configuration.

The reduced mode lacks the parent's immediate five-second batch-stall observation:
it can detect a five-second violation when a call returns, while a stuck call is
bounded by the longer outer progress watchdog. It must never replace the normal
qualification runner. Input/result hashing is correctness work retained in both
modes, not an optional probe silently removed to improve numbers.

Thirty predeclared alternating pairs were completed per scenario (120 isolated
workers total), each with a fresh one-million-event warm-up. The sparse comparison
used the first 100,000,000 canonical events; the dense control used its complete
2,000,000-event workload. All pairs matched input/result controls and exact runtime
identity. There were no failed trials. Intervals use 10,000 paired bootstrap
resamples of the median relative effect, seed 20260920. Negative effects are not
optimization gains: noise, caching and changed supervisor wake/exit-observation
delays affect the comparison. In particular, reduced telemetry can delay the
parent noticing completion by a polling interval.

| Workload / metric | Reduced p50 | Full p50 | Median paired effect | 95% interval | Diagnostic decision |
|---|---:|---:|---:|---|---|
| Sparse prefix / execution | 1.002698 s | 1.003819 s | +0.42% | -0.90% to +1.17% | Inconclusive at 1% |
| Sparse prefix / harness | 6.526949 s | 6.533444 s | +0.17% | -0.93% to +1.64% | Inconclusive at 1% |
| Dense full / execution | 0.024178 s | 0.024049 s | -0.20% | -1.60% to +0.33% | Interval below 1%; diagnostic only |
| Dense full / harness | 0.587079 s | 0.570133 s | -3.02% | -4.09% to -1.43% | Interval below 1%; diagnostic only |

**Decision:** the primary ≤1% execution-overhead target remains inconclusive.
Retain the normal runner safety probes and use external samples for attribution.
These instrumented profiles remain separate from acceptance. A shortened primary
workload and one correlated development campaign cannot establish full-volume
overhead or session independence; the dense result does not waive that evidence.

The clock calibration performed nine loops of one million monotonic clock calls:
29.34 ns/call median and 30.18 ns/call maximum observed, including Python loop
cost. Two calls per 65,521-event chunk would total approximately 0.87 ms over the
full primary workload at the maximum observed loop cost. This is only a scale
estimate, not an upper guarantee or a clock-accuracy measurement; it excludes
cache/scheduling effects. No estimated probe cost is subtracted from results.
External sampling and cProfile perturb execution and are used only for diagnosis.

## Ranked, falsifiable optimization hypotheses

| Rank / follow-on | Hypothesis and smallest experiment | Retention or rejection evidence |
|---|---|---|
| 1 / LAT-05, after relevant LAT-04 parity | Repeated event calls/state accesses and queue/book validation layout dominate sparse FIFO execution. Compare one FIFO-specialized batch traversal that keeps all field, sequence, time, capacity and checked-arithmetic validation. Preserve failure timing and partial-failure poisoning. | Retain only with at least 10% improvement in the declared prepared-chunk execution path, paired uncertainty excluding no improvement, complete independent trace equivalence, and no unexplained >5% control p95/RSS regression. Reject if costs merely move or dense/chunk-boundary controls regress. Do not start with fill math or audit materialization. |
| 2 / LAT-07 | Repeated generation passes and strided field writes dominate beyond allocation alone. Compare one fused deterministic generation pass into a bounded reusable chunk, frozen during native access, against the current vectorized construction. Hash the identical bytes inside both harness timers. Measure real storage separately when a corpus exists. | Retain only with at least 10% improvement in the affected full synthetic harness, uncertainty excluding no improvement, exact input/result controls and alias/lifetime tests. Reject if hashing dominates the residual gain, memory grows, or the byte stream/timer boundary changes. Allocation-only removal is about 4.6% of this harness and cannot alone justify a 10% claim; synthetic generator gains do not establish real-data improvements. |
| 3 / LAT-06, conditional on an actual concurrent workload | Producer per-record cursor/counter traffic inside `push_batch` limits batched handoff. Compare cached cursor/batch publication in Gambit's existing ring with the current implementation before adopting a dependency. | Require at least 10% improvement in the actual end-to-end factor handoff path with paired uncertainty, unchanged factor outputs and zero loss/reordering. Keep full/empty/wrap, partial acceptance, active lease, close/wakeup and TSan coverage; reject any tail/CPU/resource regression without an accepted tradeoff. Measure producer-limited and consumer-limited controls. |

Owners remain the proposed native/performance and data engineers in the project
plan; this diagnosis does not assign staffing or approve a new dependency. Limit
each hypothesis to three variants or five engineering days. LAT-04 independent
trace coverage is the next required slice; full-volume overhead and multi-session
qualification remain evidence gates before accepting a performance claim.

## Reproduction and retained artifacts

Use a fresh output directory for each command; outputs are never overwritten.
Run experiments serially on the identified host, without builds/tests in parallel.
The [runbook](../operations/controlled_replay.md) documents the diagnostic commands.

- [Evidence manifest and archive hashes](fifo_profile_2026-09-20/manifest.json).
- FIFO [stack summary](fifo_profile_2026-09-20/fifo-sample-stack-summary.json),
  [worker stages](fifo_profile_2026-09-20/fifo-sample-worker-result.json),
  [supervisor trial](fifo_profile_2026-09-20/fifo-sample-trial.json), and
  [complete raw sample/manifest/disassembly archive](fifo_profile_2026-09-20/fifo-sample.tar.gz).
- Ring [stack summary](fifo_profile_2026-09-20/ring-sample-stack-summary.json),
  [per-iteration metrics](fifo_profile_2026-09-20/ring-sample-ring-result.json), and
  [complete raw sample/manifest archive](fifo_profile_2026-09-20/ring-sample.tar.gz).
- Sparse [overhead summary](fifo_profile_2026-09-20/sparse-overhead-summary.json),
  [experiment/clock calibration](fifo_profile_2026-09-20/sparse-overhead-experiment.json), and
  [all 60 trial/request/worker artifacts](fifo_profile_2026-09-20/sparse-overhead.tar.gz).
- Dense [overhead summary](fifo_profile_2026-09-20/dense-overhead-summary.json),
  [experiment metadata](fifo_profile_2026-09-20/dense-overhead-experiment.json), and
  [all 60 trial/request/worker artifacts](fifo_profile_2026-09-20/dense-overhead.tar.gz).
- Full-volume Python [function table](fifo_profile_2026-09-20/python-profile-python-profile.json)
  and [raw pstats/trial archive](fifo_profile_2026-09-20/python-profile.tar.gz).

Archives contain only diagnostic reports/profiles, not generated input corpora.
Use `tar -xzf <archive>` in a new directory to inspect the original per-trial records.
The evidence manifest lists the SHA-256 and byte count of every archived file.
Sampling preceded minor diagnostic-option admission/import cleanup; exact runner
hashes are preserved per artifact, and the native binary/source remained identical.
All paired trials used unchanged runner/contract/generator/native identities.

Verification completed locally:

- **2,133 tests passed**, including diagnostic admission, result parity across
  probe modes, prefix accounting, qualification exclusion and comparison rejection
  when controls or identity differ. Existing watchdog/storage/FIFO checks passed.
- Ruff, mypy (58 source files), coverage policy, native warnings, Sphinx/document
  and notebook checks passed. All **10/10 financial mutations** were killed.
- `make check` reached the build after all preceding checks passed; sandbox DNS
  blocked isolated build dependencies. The approved `make build` retry passed,
  producing the wheel and source distribution; Twine and artifact inventory passed.
  Preserve the [original check log](fifo_profile_2026-09-20/check.log.gz) and
  [successful build retry](fifo_profile_2026-09-20/build-retry.log.gz).
- An artifact audit verified all **504 archived files**, all five archive hashes,
  both independently recomputed 30-pair summary metrics, and 22 profile/evidence
  links. Whitespace checks passed.

No native implementation was changed. This diagnostic slice did not run hosted
CI, a new sanitizer campaign, the full-volume independent oracle, full-size storage
characterization or a 200-trial performance qualification.

## Follow-on correctness evidence

LAT-04 subsequently completed the [independent full-volume synthetic comparison](fifo_parity_2026-09-20.md),
including all three primary chunk sizes and scaling/dense controls. The next
implementation slice is LAT-05; the profiling/overhead limitations above remain.
