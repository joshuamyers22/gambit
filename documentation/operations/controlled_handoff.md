# Controlled concurrent handoff

LAT-06 engineering workload: the existing NumPy batch producer → public
`TickRing` → native `TickFactorProcessor` pipeline, with the complete existing
factor snapshot checked against direct processing on every run. This characterizes
the experimental factor pipeline; it does not establish a production feed SLA or
change `gambit-fifo-latency-v1`.

LAT-08 adds opt-in [bounded batch diagnostics and shutdown reports](batch_diagnostics.md).
Use `diagnostics=True` for queue-bounded summaries; the historical `probes=True`
trace below remains available for reproducing LAT-06. Both are diagnostic modes.
The runner now shares a 250 ms cleanup deadline after its run deadline and
attaches structured failure accounting to raised worker exceptions.

The [completed LAT-06 experiment](../performance/handoff_optimization_2026-09-20.md)
retained the original ring after the user rejected the batch-publication latency
tradeoff. The candidate description below records the experiment, not current
runtime behavior.

## Frozen comparison

`benchmarks/controlled_handoff.py` uses eight million deterministic 64-byte ticks,
one instrument, batches of 1,024, capacity 65,536, 256 bounded spins and a 10 ms park
timeout for saturated throughput. Arrays, reference computation and thread
construction precede the timed region. Timing begins at release of the worker
start gate and ends after both workers drain and join. The direct control processes
the same factors and batch sizes on the calling thread. No setup, file decoding,
storage or audit persistence is included.

Separate 262,144-record controls cover copied and leased factor processing,
scheduled arrivals at one million records/s, a 2× arrival rate, and a 10 ms consumer
pause at the quarter point with capacity 2,048. Admission waits for an entire batch
of free slots. It never advances the original arrival schedule or skips inputs
when the ring fills. Rejection and partial-prefix behavior are tested separately.
All factor outputs must match exactly within and across builds; pushed/popped must
equal input volume, with zero sequence errors, dropped records or residual depth.

Record 30 fresh-process observations per build, cycling all permutations of build
order (balanced for two or three builds). Retain raw trials, input and native
hashes, process CPU, RSS, faults, context switches and wrapper counters. Report
p50/p95/p99/max and the paired median reduction with a seeded 10,000-resample
bootstrap 95% interval. Retention starts at a 10% end-to-end improvement with the
interval excluding no improvement; investigate >5% tail/RSS regressions and CPU
increases. Thirty observations are screening evidence, not a reliable production
p99 estimate. Do not pool different input sizes or declare success from upstream
integer-queue timings.

## Probes and limits

The saturated case runs with and without Python batch probes. Probed observations
are diagnostic. Completion is timestamped after a consumer call finishes all its
factors. Each record receives its containing consumer batch's completion time.
The enqueue boundary is **producer call entry**, so enqueue-to-completion includes
publication uncertainty and processing; it is not an exact queue-residence timer.
Scheduled-to-completion includes admission backlog and the consumer pause, avoiding
coordinated omission. No stage percentiles are added. Queue high-water is sampled
after producer calls and is a lower bound. Peak RSS is cumulative within a worker
and includes input/reference/warmup; allocation counts and hardware cache counters
are unavailable. These observations do not set an absolute live-market deadline.

The batch-publication candidate checks available capacity once, copies the accepted
prefix in at most two contiguous spans, then publishes the new producer cursor
with release ordering. It holds the existing wait/close mutex until publication
and counter updates finish. A concurrent `close()` can therefore wait for that
bounded copy (at most the configured capacity); a batch racing close either
publishes before close takes effect or is rejected. Consumers may drain accepted
records after close. An active lease continues to pin slots until its last view
dies. Metrics are independent atomic snapshots, exact after completed calls; they
are not a transactional view of an in-flight producer and consumer.

Thread exceptions propagate to the calling worker. Worker threads have a 30-second
deadline; the campaign additionally kills a child process after 300 seconds. Thread
timeouts invalidate the trial; the child exits, rather than allowing an unbounded
join. Other CPU-intensive checks must not run during a performance campaign.

## Reproduction

Preserve the built baseline source tree before changing the ring. Use the same
Python environment and native compiler settings for each isolated source copy:

```sh
.venv/bin/python benchmarks/build_handoff_variant.py --source /path/to/baseline --output /tmp/handoff-baseline
.venv/bin/python benchmarks/build_handoff_variant.py --source /path/to/candidate-source --output /tmp/handoff-candidate
git clone https://github.com/rigtorp/SPSCQueue.git /tmp/SPSCQueue
git -C /tmp/SPSCQueue checkout 1053918dbd251fbff69b24ef27fa5d51c29ec2af
.venv/bin/python benchmarks/build_handoff_variant.py --source /path/to/candidate-source --rigtorp /tmp/SPSCQueue --output /tmp/handoff-rigtorp
.venv/bin/python benchmarks/controlled_handoff.py campaign \
  --variant baseline=/tmp/handoff-baseline/python \
  --variant candidate=/tmp/handoff-candidate/python \
  --variant rigtorp=/tmp/handoff-rigtorp/python \
  --trials 30 --output /tmp/handoff-results
```

Keep compiler output with the build manifest. Outputs must be new directories
outside the source tree. The helper copies the existing package and rebuilds only
the factor extension with setup.py's C++11/optimization/sanitizer settings; it does
not change installed binaries. `--sanitize` creates an ASan/UBSan build. Run the
extended `tests/cpp/tick_ring_tsan.cpp` probe separately under ThreadSanitizer.
On macOS, inject the ASan runtime into Python directly with `PYTHONPATH` pointing
at the sanitized `src` tree; launching through a system shell wrapper can strip
the `DYLD_INSERT_LIBRARIES` setting before Python starts.

The [Rigtorp adapter](../../benchmarks/rigtorp_ring_adapter.hpp) is benchmark-only.
It uses pinned upstream `try_push`, `front`, `pop` and `size` inside Gambit's complete
wrapper, including counters, close and notification. Its safe public-API lease is
one record long; upstream does not expose Gambit's contiguous batch-span contract.
The helper verifies the header SHA-256 and copies the MIT license. No runtime
dependency is introduced. A different adapter or queue revision is a new variant.

The current tree contains the original ring. To reproduce a rejected variant,
make an isolated copy of the built package and replace only its `spsc_ring.hpp`
and `tick_ring.cpp` with the corresponding files in the report's
`variant-sources.tar.gz`, then pass that copy as `--source`. The recorded first
campaign used the cached-tail variant's counter-batched wrapper for Rigtorp;
the second compared baseline, local cursor and batch publication. Do not combine
the two baselines into a single paired result. Later wrapper builds were build
checks only and are not additional performance evidence.
