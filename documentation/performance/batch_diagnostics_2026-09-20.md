# LAT-08 batch metrics, queue health and bounded shutdown

Date: 2026-09-20. **LAT-08 is complete for engineering acceptance.** Scope: the existing experimental factor handoff and
controlled FIFO replay. The native ring and FIFO implementation remain unchanged.
New metrics are explicit diagnostic options; no production latency qualification
or logging dependency is introduced.

## Delivered behavior

- Fixed 65-bucket batch-call timing summaries and record coverage, with exact
  observed maxima and explicitly labeled quantile upper bounds.
- Queue-capacity-bounded timestamp metadata, sampled high-water, full observations,
  admission-wait episodes/polls, enqueue-probe-to-completion age and original
  scheduled-offer age. No per-record clock or completed-volume trace is required.
- Structured handoff failure reports, accepted/unaccepted and confirmed completed
  counts, exception-safe lease release, shared run/cleanup deadlines and explicit
  live-worker outcomes. Failed runs never return successful factor snapshots.
- FIFO batch histograms using existing native-call clocks. Supervisor results
  include shutdown outcome, forced termination, cleanup duration/bound and last
  confirmed progress on failure.

The [runbook](../operations/batch_diagnostics.md) defines the API, boundaries,
ownership, failure handling and reproduction commands. Histograms describe batches
or producer/consumer batch overlaps. They are not LAT-06's exact record-weighted
age percentiles. A sampled queue maximum is a lower bound. Slow or paused consumers
retain the original offer schedule, exposing admission backlog rather than moving
the measurement's starting point.

## Handoff overhead: retain as opt-in diagnosis

The final-source screen used thirty paired fresh-process observations alternating diagnostics off/on, with
8,000,000 ticks for saturated processing and 262,144 for each copy, lease,
1-million/s schedule, 2-million/s burst and stalled-consumer control. All cases
matched their complete direct factor snapshots and drained every record. The
10 ms consumer stall used queue capacity 2,048; other controls used 65,536.
Batch size was 1,024, with the original ring, 256 spins and 10 ms maximum park.

Whole-handoff wall time includes worker release, transfer, factors, joins and
reported cleanup. Input/reference/warmup construction precedes this boundary and
remains in process RSS. The paired median overhead is computed per pair; it is
not the ratio of the two displayed marginal medians.

| Control | Off p50, ms | On p50, ms | Paired median wall overhead | 95% interval |
|---|---:|---:|---:|---:|
| Saturated | 190.526 | 202.945 | +13.90% | +9.74% to +23.14% |
| Copy | 14.623 | 15.066 | +1.53% | −3.91% to +7.81% |
| Lease | 8.329 | 8.767 | +7.38% | −1.92% to +22.29% |
| Scheduled 1 million/s | 261.491 | 261.505 | +0.00% | −0.01% to +0.01% |
| Burst 2 million/s | 130.848 | 130.891 | +0.04% | −0.01% to +0.08% |
| Stalled consumer | 130.862 | 130.881 | +0.01% | −0.02% to +0.06% |

Saturated p95 was 458.088 → 282.901 ms, with slow baseline observations retained;
this is not a tail-improvement claim. Paired CPU increased 12.44%.
Scheduled wall time is constrained by the offered rate; this does not imply free
instrumentation. Scheduled/burst/stall CPU increased 7.84%/22.08%/12.45%.
Whole-worker peak RSS p95 remained around 687–700 MiB across successive cases,
including the 512 MB prepared tick array and reference work. These are factor
pipeline observations, not a FIFO 512 MiB qualification result.

The **1% instrumentation target is exceeded on the saturated path**; copy/lease
wall overhead is inconclusive at that threshold.
Keep detailed health diagnostics opt-in and report their timings separately from
acceptance. Never subtract estimated overhead. The original ring remains the
transport. A Quill/fmtlog trial is unnecessary: these summaries need no per-record
formatting, logging sink or asynchronous log queue. A logger would not remove the
measured timestamp/sidecar cost. Existing external sampling remains available for
investigating the uninstrumented path.

In the first final-source instrumented stall observation, the sampled queue reached its exact
capacity of 2,048, admission blocked in 4 episodes, and the largest pending
timestamp coverage was 3,072 records (capacity plus one batch). Maximum enqueue
age was 10.564 ms; scheduled-offer age was 10.675 ms. All records completed and
the sidecar drained to zero. These illustrate signal behavior, not latency limits.

An earlier 30-pair handoff screen, before failure-path hardening and stricter
sidecar argument validation, found +20.22% saturated overhead (95% interval
+17.45% to +31.82%). It is retained separately with its measured source snapshot;
it is not pooled with the final-source screen or discarded. Both exceed the
instrumentation budget. The final screen repeats after the implementation fixes.

## FIFO batch histogram overhead

Each FIFO screen has 30 alternating pairs, with the same input, chunk size,
native binary, original loader, stage probes and reference controls. Only the
optional batch histogram changes. It reuses the existing native-call timestamps;
its update is inside the harness and outside the prepared execution sum. Effects
on subsequent batch execution remain visible rather than being subtracted.

| Screen | Execution off/on p50, seconds | Paired execution overhead, 95% interval | Paired harness overhead, 95% interval |
|---|---:|---:|---:|
| Earlier 100-million primary prefix | 0.434727 / 0.433240 | +0.49% [−1.75%, +1.73%] | +0.29% [−2.01%, +1.06%] |
| Earlier full 2-million dense | 0.018542 / 0.018429 | −1.57% [−3.91%, +0.24%] | −1.14% [−2.20%, −0.44%] |
| Final-source 10-million primary prefix | 0.042862 / 0.042359 | −1.21% [−3.17%, +0.53%] | −0.87% [−2.26%, −0.50%] |
| Final-source full 2-million dense | 0.018167 / 0.018430 | +2.45% [−0.28%, +3.71%] | −0.18% [−1.41%, +0.39%] |

The earlier screens precede the watchdog/lease hardening and stricter sidecar
argument checks; their source snapshot is retained separately. Final-source
screens repeat after those fixes. All FIFO input/result controls matched, and the
dense runs checked the existing canonical controls. Negative observed effects
are not claimed as optimization gains. Development noise and session dependence
remain; different prefixes and source snapshots are not pooled. The longer prefix
and final dense execution intervals leave the 1% target inconclusive. No full
946,944,000-record probe qualification was performed. Histograms stay opt-in.

## Failure and shutdown validation

Fault tests cover cancellation before publication, partial acceptance, copied and
leased factor failures, a stuck producer, finite option admission, malformed
protocol, missing completion, RSS/batch/setup deadlines, and a helper retaining
stdout after its worker exits. Both worker joins share the run deadline and then
the cleanup deadline. Incomplete joins suppress final accounting and require
process containment. The FIFO watchdog now terminates the process group even
when the leader has already exited, closing the inherited-pipe loophole.

A failing lease-consumer test exposed traceback-held NumPy references that kept
the lease active after `finally`. Clearing unwound exception-frame locals fixes
that retention without forcing release of a live view. The exception type and
stack/line information remain. The initial failure and passing repeat are retained
in the validation logs. Fixed-size histogram tests and a 10,000-batch reuse test
verify that completed volume does not grow diagnostic storage; zero/negative
reservations cannot bypass the sidecar capacity bound.

Final validation passed **2,283 tests** and all `make check` checks: lint, typing,
coverage policy, financial mutation checks, native warnings, documentation,
notebook cleanliness, wheel/sdist build and artifact verification. The focused
batch/ownership/watchdog suite passed **82 tests**. No native implementation was
changed; no new native sanitizer campaign is claimed.

## Evidence and limits

Raw on/off results retain CPU, RSS, page faults, context switches, native and input
hashes, complete factor snapshots, queue counters and shutdown outcomes. Summary
quantiles across trials use nearest rank; bootstrap intervals use 10,000 paired
resamples with seed 20260920. Thirty observations provide a development screen;
p99 equals the maximum and is descriptive. There is no multi-session or production
tail claim. Runs were serial without concurrent tests/builds, on the same Apple M4
development host used for LAT-07. No hardware-counter or allocation-count result
is claimed.

Across both rounds, **360 workers completed 960 cases and 7,957,286,400 measured
records**, excluding warmups and untimed direct references. Every worker succeeded;
all input/result/factor controls and queue accounting checks passed. The largest
observed successful handoff cleanup was 0.171 ms against the configured 250 ms
cleanup deadline. This observation is not a hard worst-case guarantee.

The [compact evidence](batch_diagnostics_2026-09-20/) retains both rounds, source
snapshots, summaries and validation logs. Full local evidence is under
`/Users/jkm0607/Projects/gambit-evidence/lat08-2026-09-20`. Native SHA-256 remained
`9bb6074a0149a302edd4a1f8ab3063f3ae830b15418342738dcacfde450ffb39`.

The new diagnostics are disabled by default. Native code was not changed, so prior
FIFO parity and ring rejection decisions remain applicable; no new independent
full-volume trace campaign is claimed. LAT-09 must still resolve full-volume probe
uncertainty and collect the required qualification evidence.

Next: **LAT-09—qualification evidence and an explicit promotion, retargeting or remain-experimental decision**.
