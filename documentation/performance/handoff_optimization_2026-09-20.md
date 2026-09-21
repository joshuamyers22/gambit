# LAT-06 concurrent factor handoff

Date: 2026-09-20. **Experiment complete; original ring retained.** The user selected
Gambit's existing factor pipeline and explicitly chose to retain the original ring
after reviewing the batch-publication throughput/latency tradeoff. No native
optimization or Rigtorp runtime dependency remains. LAT-05's FIFO optimization is
unchanged. This closes the bounded LAT-06 investigation, not a claim that concurrent
handoff latency improved or that production qualification passed.

## Workload and retained work

Added a [controlled runner](../../benchmarks/controlled_handoff.py), an
[isolated build helper](../../benchmarks/build_handoff_variant.py), a pinned
[Rigtorp adapter](../../benchmarks/rigtorp_ring_adapter.hpp), and a
[reproduction runbook](../operations/controlled_handoff.md). The runner propagates
thread failures, enforces thread/process deadlines, checks complete factor
snapshots and accounts for every input. Tests now cover those failure paths,
scheduled backlog, partial acceptance around leases/close, and racing close/drain.
The native TSan probe covers pop, batch consume and retained read spans with
64-byte payload verification at capacities 2, 16 and 1,024.

The existing batch producer → `TickRing` → `TickFactorProcessor` pipeline is the
engineering workload. Eight million deterministic ticks, one instrument,
1,024-record batches, capacity 65,536, 256 spins and a 10 ms park timeout form the
primary saturated case. Direct processing uses identical factors and batches.
Copy/lease controls use 262,144 records and the same complete native factor
calculation. Other controls schedule 262,144 records at one million records/s,
2× that rate, and 2× with a 10 ms consumer stall and capacity 2,048. Arrival schedules
never shift when admission blocks. All accepted records must drain in order.

The host was the existing Apple M4, macOS 15.5 ARM64, Python 3.10.20, NumPy 2.2.6,
Apple Clang 17, C++11/O3 environment. Timers exclude input creation, reference
calculation and thread construction and include the complete worker drain/join.
CPU-intensive correctness/build work ran outside performance campaigns.

Two campaigns each ran 30 rounds of three fresh-process builds, cycling all six
build-order permutations: **180 workers and 1,440 measured cases**. Within each
round, input hashes and every factor snapshot matched across builds. Every handoff
had zero loss, rejection, residual depth and sequence errors. Raw distributions,
identities and source snapshots are retained below. Intervals are paired median
reductions with seeded 10,000-resample bootstrap 95% intervals; 30 observations
are development screening, not production p99 qualification.

## Three source variants, no adoption

| Variant | Primary paired wall reduction, 95% interval | Decision |
|---|---:|---|
| Cached consumer cursor + one producer counter update per batch | 1.12% [−1.19%, 3.61%] | Reject: below 10%; lease path regressed 21.8% |
| Also keep producer head local within the batch, retain per-record publication/close checks | −6.96% [−11.35%, 0.24%] | Reject: no primary benefit; lease path regressed 17.6% |
| Publish an accepted batch together; serialize publication against close with the existing wait mutex | 4.49% [1.05%, 8.01%] | Reject: below primary wall threshold and unacceptable saturated age regression |

The pinned [Rigtorp SPSCQueue revision](https://github.com/rigtorp/SPSCQueue/tree/1053918dbd251fbff69b24ef27fa5d51c29ec2af)
was `1053918dbd251fbff69b24ef27fa5d51c29ec2af`; header SHA-256 was
`1e631ec9e8ba4955da5cac116620815055f7da0ea936bfdb3036c4e87bc8a6e8`.
It ran inside the cached-tail candidate's complete counter-batched wrapper,
including close, waits and notification. Primary paired reduction was
−4.54% [−9.63%, 1.65%]. Its public `front` API permitted only a one-record lease in
the safe adapter; the measured lease path was about 81× slower than baseline.
This is a wrapper/contract comparison, not a claim about upstream's integer-queue
microbenchmark. The MIT header/license and exact adapter are archived.

The batch-publication candidate copied at most two contiguous spans, published
once with release ordering, and accounted for accepted/rejected records once.
The existing wait mutex kept close from overtaking unpublished data; close could
wait for the accepted copy, bounded by ring capacity. Active leases still pinned
slots. Its copy/lease gains were real, but they did not remove the latency tradeoff:

| Complete path, second campaign | Baseline p50 | Batch publication p50 | Paired reduction, 95% interval |
|---|---:|---:|---:|
| In-place, 8 million records | 166.653 ms | 160.136 ms | 4.49% [1.05%, 8.01%] |
| Copy + factors, 262,144 records | 16.249 ms | 12.143 ms | 24.42% [18.30%, 30.36%] |
| Lease + factors, 262,144 records | 8.221 ms | 2.608 ms | 67.97% [62.39%, 69.80%] |

In-place CPU fell 11.7%, but the instrumented saturated queue filled: median
sampled high-water rose from 2,048 to 65,536 records. Median trial
enqueue-call-to-factor-completion p99 rose from **42.104 μs to 1,853.813 μs**.
The maximum observed age rose from 324.500 μs to 3,875.625 μs. The stalled-consumer
control's corresponding median p99 also rose from 146.230 to 215.105 μs. Scheduled
offer-to-completion distributions remained similar because they include admission
backlog as well as queueing. Faster insertion moved waiting into the queue; it did
not establish a general handoff latency improvement.

This age timer is diagnostic: enqueue is producer call entry, and completion is
the containing consumer batch's factor completion. It includes publication and
batch granularity, not exact per-record native residence. The probe-on primary
showed an 18.7% paired wall reduction, whereas probe-off showed 4.49%; Python probes
change scheduling and cannot be used to qualify the uninstrumented path. No
stage percentiles are added. RSS includes input/reference/warmup and is cumulative
within each worker (about 1.23 GB here); allocation/hardware counters were not
collected. The direct control's p99 also rose from 45.253 to 52.010 ms, an unresolved
screening outlier despite bypassing the ring. No claim of universal nonregression
is made, and no further tuning was attempted after the three-variant limit.

The [LAT-03 rule](fifo_profile_2026-09-20.md) requires rejection of tail/CPU/resource
regressions without an accepted tradeoff. The user explicitly rejected this
tradeoff and selected the original ring. Source and installed native binary were
restored from the pre-LAT-06 snapshot; the restored extension SHA-256 is
`9bb6074a0149a302edd4a1f8ab3063f3ae830b15418342738dcacfde450ffb39`.

## Validation and evidence

The candidate passed 27 focused tests, ASan/UBSan and the 3.6-million-record TSan
probe. An additional 100 close/publication races preserved exact accepted-prefix
bytes and counters; measured close maximum was 0.321 ms on the configured capacity,
not a production shutdown guarantee. Six independent full-volume FIFO trace cases
also matched on that candidate binary: 3,475,416,000 events. This is correctness
evidence only, collected separately from performance.

The final retained baseline passed **2,208 tests**, **27 ASan/UBSan handoff tests**,
the expanded 1.8-million-record TSan probe, native warnings, lint, typing,
coverage/mutation policy, documentation and release artifact checks (`make check`).
Final counts and log hashes are in the evidence manifest. Linux CI retains its
TSan job; local sanitizer evidence here is macOS ARM64, not an unexecuted Linux run.

Compact evidence in [the evidence directory](handoff_optimization_2026-09-20/)
contains both raw trial archives and summaries, source variants, upstream pin and
license, build identities, validation logs, parity summaries, the decision record
and SHA-256 manifest. Full builds, binaries and FIFO trace arrays remain under
`/Users/jkm0607/Projects/gambit-evidence/lat06-2026-09-20` and are hashed by the
external evidence manifest. The restored binary is the same baseline previously
validated by LAT-05; the additional candidate FIFO campaign is labelled separately.

Next is **LAT-07: measure and reduce load/decode/copy costs**. LAT-06 leaves a
repeatable comparison and rejection decision for any future workload-specific
queue work. Reopen it only with a new measured hypothesis or an explicitly accepted
throughput/age tradeoff. Experimental maturity and LAT-09 qualification remain open.
