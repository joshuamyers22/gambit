# Controlled FIFO replay runner

The repository command is `benchmarks/controlled_replay.py`. It implements LAT-02
measurement and worker containment for [contract v1](../../LATENCY_BUDGET.md).
The original `top_of_book_backtest.py` remains available and its generator is
unchanged. Install frozen developer dependencies with `uv sync --frozen --all-extras`.
`psutil` is an explicit developer dependency for independent worker RSS sampling.

LAT-08 adds optional [batch histograms and structured shutdown reporting](batch_diagnostics.md).
Set `diagnostic={"batch_metrics": True}` through `run_trial` for bounded native-call
summaries; these runs remain ineligible for qualification. Supervisor reports retain
last confirmed progress on failure, termination outcome and cleanup bounds.

## Quick verification

Run from the checkout root with its built native extension:

```sh
.venv/bin/python benchmarks/controlled_replay.py run \
  --scenario fifo-smoke-v1 --trials 3 --session-id dev-smoke \
  --output-dir /tmp/gambit-fifo-smoke
```

Output directories must be new. Default execution is the 100,003-record dense
smoke case, never the 946.9-million-record primary. `--ticks` changes only the
bounded smoke case (1–1,000,000), and cannot reduce a canonical qualification
workload silently. Chunk size is checked before worker admission; the primary
five-second decision uses the default 65,521. Alternate chunks are controls.

Supported replay scenarios: `fifo-3y-sparse-v1`, `fifo-2y-sparse-v1`,
`fifo-1m-dense-v1`, `fifo-2m-dense-v1`, and `fifo-smoke-v1`. Adversarial policy
fixtures remain in `tests/test_fifo_backtest.py` and `tests/test_top_of_book_backtest.py`;
they are correctness gates, not successful replay timings.

## Storage-backed input

```sh
.venv/bin/python benchmarks/controlled_replay.py prepare \
  --scenario fifo-smoke-v1 --output-dir /tmp/gambit-fifo-data
.venv/bin/python benchmarks/controlled_replay.py run \
  --scenario fifo-smoke-v1 --dataset /tmp/gambit-fifo-data \
  --trials 3 --session-id dev-storage --output-dir /tmp/gambit-fifo-storage
```

Preparation streams canonical records to `events.bin`, then exclusively publishes
`manifest.json` with schema, workload, generator identity, byte size, layout and
SHA-256. Incomplete preparation leaves bytes plus a failure record, without a
usable manifest. Replay checks metadata/length before processing and hashes all
actual consumed bytes before result publication. Truncation, growth or corruption
fails the trial. NumPy reads allocate owned bounded chunks, marked read-only;
no shared writable mmap or background input mutation is used. Keep source files
immutable during replay; concurrent changes cannot be certified as a snapshot.

Full primary input occupies **83,331,072,000 bytes** before filesystem overhead.
Choose authorized storage with sufficient capacity before preparing it. A
storage workload retains its underlying scenario ID and has `source.kind=storage`.
It does not qualify for the synthetic full-harness time gate.

Default cache state is `uncontrolled`. `--cache-state operator-cold` or
`operator-warm` requires `--cache-evidence '...'` explaining the external cache
preparation and observation. These are operator declarations; the runner does
not evict system caches, infer residency from a mapping, or certify a cold run.
Compare only compatible cache/storage conditions. Collect cold/warm measurements
on the actual intended storage during LAT-07; smoke data is not storage-capacity
evidence for the full corpus.

## Repetitions, sessions and build evidence

For a characterization baseline, omit approval evidence and run the desired
scenario/repetitions. Measurements still report correctly but are explicitly
ineligible for the v1 timing decision.

```sh
.venv/bin/python benchmarks/controlled_replay.py run \
  --scenario fifo-3y-sparse-v1 --trials 1 --session-id initial-characterization \
  --output-dir /tmp/gambit-fifo-primary-baseline
```

Qualification collection consists of **four separately prepared sessions of 50
trials**. Do not launch the sessions in parallel or selectively retain fast runs.
Before each session, record a new conditions file. Example shape (replace the
times/notes with actual observations):

```json
{
  "operator": "Josh Myers",
  "quiet_period_start_utc": "2026-09-20T18:00:00+00:00",
  "quiet_period_end_utc": "2026-09-20T18:05:00+00:00",
  "background_workloads": "Record what was running and what was stopped",
  "power_thermal_notes": "Record power source, mode and thermal observations",
  "independence_notes": "Record session separation and possible correlated interference",
  "ac_power": true,
  "low_power_mode": false
}
```

The quiet interval must span at least 300 seconds and end within five minutes
before the command starts. Host/runtime facts and before/after power/thermal/load
observations are also captured automatically. Unavailable facts remain null;
they are not filled in from the approved host description. Operator notes do not
constitute independent review of session independence.

Exact compiler/link invocation evidence must come from the actual native build,
not the installed compiler's version. Supply a build JSON with:

- `compiler_version`: compiler output including the version/build identifier;
- `compile_commands` and `link_commands`: arrays of full argument arrays;
- `native_extension_sha256`: SHA-256 of the imported native extension;
- `source_sha256`: SHA-256 of `src/gambit/cpp/factor_cache/top_of_book_backtest.cpp`.

The runner checks artifact/source identity and v1 compiler/flag eligibility.
It retains the supplied record; it cannot independently prove the recorded
commands produced that binary. Review this evidence alongside the build logs.
No build manifest means measurement only. Rebuild/package qualification belongs
to the same-commit release gates; the benchmark does not rebuild implicitly.

```sh
.venv/bin/python benchmarks/controlled_replay.py run \
  --scenario fifo-3y-sparse-v1 --trials 50 --session-id qualification-session-1 \
  --session-evidence /tmp/session-1.json --build-manifest /tmp/native-build.json \
  --output-dir /tmp/gambit-fifo-session-1
```

Repeat with distinct session IDs and new evidence/output directories. Then:

```sh
.venv/bin/python benchmarks/controlled_replay.py summarize \
  /tmp/gambit-fifo-session-1 /tmp/gambit-fifo-session-2 \
  /tmp/gambit-fifo-session-3 /tmp/gambit-fifo-session-4 \
  --output /tmp/gambit-fifo-qualification-summary.json
```

Aggregation rejects duplicate trials and incomplete campaigns. Mixed workload,
chunk, source, runtime, build or variant identities cannot produce a passing
primary decision. Four or fewer misses of each p95 limit are permitted out of
200, with every observation below its maximum and RSS ceiling. Nearest-rank
p50/p95/p99, maximum and jitter are reported; p99 is descriptive and p99.9 is
unqualified. `timing_gate=pass` still leaves
`qualification=pending_independent_evidence`: independent full-volume trace
parity, instrumentation/independence review, platform/sanitizer checks and owner
disposition are separate requirements. The runner cannot approve them.

## Paired screening

Use a separate baseline environment containing the intended built Gambit revision
and the frozen developer dependencies. The worker imports Gambit from the selected
Python environment; it does not mutate either checkout or use `git checkout`.
Verify the recorded source and binary identities identify the intended variants.

```sh
.venv/bin/python benchmarks/controlled_replay.py run \
  --scenario fifo-1m-dense-v1 --trials 30 --session-id dense-screen \
  --baseline-python /path/to/baseline/.venv/bin/python \
  --output-dir /tmp/gambit-fifo-paired
```

The order alternates baseline/candidate then candidate/baseline for 30 pairs.
Optional `--baseline-build-manifest` records baseline provenance. The report
retains per-variant distributions, matched-pair execution reduction and a
deterministic 10,000-resample bootstrap interval for its median. Mismatched
host/runtime, inputs or output controls make the comparison inconclusive. A
retention signal needs at least 10% median reduction, a positive lower confidence
bound, and no greater than 5% p95 regression in execution, harness or RSS.
This is a screening signal, not a promotion or proof of statistical independence.

## Failure containment and timing

Each trial starts a fresh subprocess with a fresh disposable one-million-event
warm-up engine followed by a fresh timed engine. No terminal state is reused.
The parent independently polls RSS and protocol state every approximately 20 ms;
the worker also checks process high-water RSS between chunks and around results.
Short memory spikes are reflected in the worker's high-water metric even if the
parent's sampling misses them. This is observed resource enforcement, not an OS
hard address-space cap.

The supervisor enforces setup/warm-up, native batch, progress, harness and exit
deadlines, requests cooperative stop, then kills/reaps a stuck worker within the
bounded grace period. A native GIL release or stuck native loop cannot block the
parent watchdog. No partial replay result is reported as a successful trial.
Worker payloads, stderr and failure records remain for diagnosis. Any failed
trial stops the campaign; retry into a new directory and retain the prior one.

Ctrl-C cancels the current worker. For an unattended run, supply
`--cancel-file /tmp/gambit-stop`; creating that file requests cancellation. Do not
reuse a preexisting stop file unintentionally. Workers check cancellation between
chunks; native calls exceeding the grace period are terminated by the parent.

The prepared-chunk execution sum includes initialization, timed native calls and
result construction. Per-stage generation/load, input/result hashing, ledger
checks, warm-up, faults, CPU, context switches and logical bytes are separate.
The supervisor's continuous harness timer conservatively ends after observed
worker exit, including report close and cleanup/polling overhead. Persistence
time is recorded independently; no fsync durability is claimed. Storage metadata
verification is included in its full-harness wall time, and consumed-byte digest
verification completes before success. Unmeasured allocation counts/hardware
counters are null, not zeros.

Batch protocol and resource probes can perturb cache/scheduling. The LAT-03
comparison measured a +0.42% median sparse-prefix execution effect, with a 95%
interval of −0.90% to +1.17%; the 1% target remains inconclusive. Resolve primary
overhead and session-independence evidence before treating a timing pass as qualified.
The runner never subtracts estimated probe cost from acceptance measurements.

## Diagnostic profiling (LAT-03)

Use `benchmarks/profile_replay.py` for diagnosis. It never contributes eligible
qualification trials. See the [2026-09-20 profile and overhead report](../performance/fifo_profile_2026-09-20.md)
for measured costs, uncertainty, ranked hypotheses and retained raw artifacts.

```sh
# macOS native stacks: sample the replay worker, then the separate factor ring.
.venv/bin/python benchmarks/profile_replay.py sample --path fifo --seconds 20 \
  --output-dir /tmp/gambit-fifo-profile
.venv/bin/python benchmarks/profile_replay.py sample --path ring --seconds 20 \
  --output-dir /tmp/gambit-ring-profile

# Python stage attribution over the full primary workload.
.venv/bin/python benchmarks/profile_replay.py python --prefix-ticks 946944000 \
  --output-dir /tmp/gambit-python-profile

# Thirty alternating pairs per scenario; run campaigns serially.
.venv/bin/python benchmarks/profile_replay.py overhead --pairs 30 \
  --prefix-ticks 100000000 --output-dir /tmp/gambit-probe-overhead-sparse
.venv/bin/python benchmarks/profile_replay.py overhead --pairs 30 \
  --scenario fifo-2m-dense-v1 --prefix-ticks 2000000 \
  --output-dir /tmp/gambit-probe-overhead-dense
```

Every directory must be new. The sampler requires macOS process-inspection
permission; it identifies the native child rather than sampling its supervisor.
Sampler duration is bounded to 1–30 seconds. FIFO sampling still finishes the
full canonical replay. The ring driver exercises the existing in-place factor
benchmark with one million read-only records per iteration, batch 1,024 and
capacity 65,536. It is a separate path, not an alternative FIFO workload. Other
platforms must use an appropriate external profiler and record a separate host
identity; this driver does not silently substitute a different sampler.

Overhead screening is bounded to 1–30 pairs, and fewer than 30 yields
`insufficient_pairs`. Prefixes must be positive and within the scenario count;
storage prefixes are rejected because they cannot verify the complete input
manifest. Every pair must have identical workload, source, runtime/build identity,
prefix and input/result controls. Order alternates to reduce systematic drift.
The report uses the median of paired `full / timers_only - 1` effects and a
seeded 10,000-resample paired-bootstrap 95% interval. Crossing the 1% threshold
is inconclusive. Correlated development-session noise and a shortened primary
workload prevent qualification even when the diagnostic interval lies below 1%.

`full` retains the normal runner. `timers_only` disables optional generation/hash
stage clocks, worker per-batch RSS syscalls and batch/progress messages, keeping
approximately one-second heartbeats. Both modes preserve native validation,
input/result hashing, ledger checks, the two native-call clock reads, parent RSS
polling, cancellation, and outer progress/harness watchdogs. Missing stage times
are null. With no batch-start message, the reduced mode cannot provide the
parent's immediate five-second native-stall termination: it rejects long calls
when they return, and a stuck call remains subject to the longer progress bound.
Use it only as a diagnostic comparison; normal runner safety behavior is retained
for every ordinary benchmark. Never subtract estimated clock/probe cost.

Diagnostic trial/request/worker records contain `diagnostic` and actual
`measured_ticks` (the latter in the worker result); the declared canonical workload
is retained separately. Canonical full-volume controls are checked only when the
complete workload ran. All diagnostic modes are blocked from qualification by the
summary contract. cProfile results are saved as pstats plus a JSON function table;
native calls remain opaque, so use the separate native stacks to attribute them.
Raw stack counts include sleeping threads and must not be called CPU percentages.

## Artifact format and validation

All JSON reports use schema `gambit-controlled-replay-v1` and contract
`gambit-fifo-latency-v1` (the outer campaign summary groups per-variant reports).
Every output is exclusively created, without overwriting previous evidence:

| Artifact | Meaning |
|---|---|
| `campaign.json` | Planned workload, trial count, session and start metadata |
| `NNN-variant/request.json` | Exact worker workload/input/build request |
| `NNN-variant/worker-result.json` | Successful reconciled native measurement; absent on validation failure |
| `NNN-variant/trial.json` | Authoritative supervisor outcome, limits, identities, condition observations, measurements or failure |
| `NNN-variant/stderr.txt` | Worker diagnostic output |
| `summary.json` | Observed distributions, incomplete campaign status, timing eligibility and optional paired comparison |

A worker result alone never establishes success: process exit, RSS and watchdog
failures override it in `trial.json`. Directory existence alone does not establish
a complete campaign. Keep inputs/results outside Git when large or restricted;
retain immutable raw artifacts and their hashes for consequential comparisons.

Focused verification:

```sh
.venv/bin/python -m pytest -q tests/test_controlled_replay.py \
  tests/test_top_of_book_benchmark.py tests/test_fifo_backtest.py \
  tests/test_latency_budget_contract.py tests/test_replay_profiling.py
```

The performance smoke marker exercises real isolated synthetic/storage workers
without host-dependent time assertions. Fault tests cover malformed/corrupt data,
worker timeouts, cancellation, memory-limit failure, incomplete protocols and
qualification misclassification. Shared CI runs those checks, not the numerical
latency thresholds.

## Independent full-volume correctness evidence

LAT-04 completed the baseline synthetic comparison; see the [parity runbook](fifo_parity.md)
and [full-volume result](../performance/fifo_parity_2026-09-20.md). Controlled performance
workers do not run or automatically ingest that external oracle campaign. Their
`full_volume_independent_trace_parity=false` records that fact for the trial;
qualification still needs the matching binary/workload/input evidence bundle and
the other acceptance gates. A changed native build must repeat parity.
