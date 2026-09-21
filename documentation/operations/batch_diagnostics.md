# Batch diagnostics and shutdown reports

LAT-08 covers the existing experimental FIFO replay and batch-producer/native-factor
handoff. The original native ring and FIFO implementation are unchanged. Diagnostics
are opt-in and remain separate from performance qualification.

## Concurrent factor path

Run a bounded on/off screen from the repository root:

```sh
.venv/bin/python benchmarks/batch_observability.py campaign \
  --pairs 30 --output /tmp/gambit-batch-metrics
.venv/bin/python benchmarks/batch_observability.py worker --mode on --ticks 8192
```

The worker processes prepared NumPy ticks through the existing public `TickRing`
and `TickFactorProcessor`. Every successful case must match the complete direct
factor snapshot and drain every input without rejection or sequence errors.
Saturated, copied, leased, scheduled, double-rate and stalled-consumer controls use
the LAT-06 workload. Whole-worker RSS includes input, reference and warmup memory.
It is not a FIFO 512 MiB qualification measurement.

For a single engineering measurement, import `benchmarks.controlled_handoff` and
call `measure(records, diagnostics=True)`. `diagnostics=False` is the default.
The returned `diagnostics` contains:

- Producer and consumer batch-call counts, completed record counts, summed call
  durations, exact maxima and fixed histograms. Consumer durations include waiting
  for input. Producer durations include the admission probe and metadata reservation.
- Sampled queue high-water, observations of an exactly full queue, admission-wait
  episodes and poll counts. An episode starts when a batch lacks enough free slots;
  this can occur before the queue is completely full. These are pressure signals,
  not record drops. Original native rejection/wait counters remain in `metrics`.
- Enqueue-probe-to-completion age, scheduled-offer-to-completion age and admission
  lateness. The producer timestamps immediately before reserving diagnostic metadata
  and calling `push_batch`. This includes instrumentation/publication uncertainty and
  factor processing; it is not exact native queue residence or exchange event age.
  Scheduled offers never move forward when the queue blocks.

There are 65 timing buckets with inclusive upper bounds `0, 1, 3, 7, ...,
2**64-1` nanoseconds. Reported p50/p95/p99 values are **bucket upper bounds**;
maxima are exact observations. Age samples represent producer/consumer batch
overlaps, not independently timestamped records. `records` counts their total
coverage separately. Do not compare these bucketed batch quantiles directly with
LAT-06's exact record-weighted diagnostic percentiles.

Timestamp metadata never exceeds queue capacity plus one consumer batch, including
a popped batch whose completion is being reported. Histograms have fixed storage,
and completed metadata is released. Exhaustion is an explicit failure, never
silent eviction. The sidecar lock is never held during a native call or wait.
Only quiescent workers supply final diagnostic snapshots. Queue high-water is a
sampled lower bound. A live thread prevents final accounting, rather than producing
a misleading zero depth or successful drain.

The older `probes=True` option remains available for reproducing LAT-06's full
record-weighted trace. Its arrays scale with input volume; it is distinct from
the bounded LAT-08 `diagnostics=True` summaries.

## Shutdown and failures

The handoff's default run deadline is 30 seconds. Both joins share that deadline;
after requesting stop and closing the ring they share a further 250 ms cleanup
deadline. `cleanup_timeout` admits 0–2 seconds; `timeout` admits up to 300 seconds.
Scheduled waits and injected consumer stalls respond to stop. A retained lease
view is released in `finally` on factor failure. An accepted prefix is never
presented as successful completion of the input.

Successful results contain `shutdown.status = "joined"`. Raised worker exceptions
retain their original type and attach `handoff_report`, containing the failure
stage/type and bounded message, input and confirmed completed counts, native queue
counters, accepted-but-uncompleted records, unaccepted input and cleanup outcome.
Only fully returned consumer batches count as completed after an exception;
partial native work is not inferred. Failed reports have no successful factor
snapshot. Unwound exception-frame locals are cleared so a traceback alone cannot
pin a leased array; exception type and stack/line information are preserved.
An incomplete join reports live thread names and
`process_isolation_required = true`; final counters/diagnostics are unavailable.

Python cannot terminate an arbitrary native call or thread. Use the CLI's isolated
worker for containment: each worker has a 120-second parent process deadline, and
a timed-out child is killed and reaped. Never reuse an in-process measurement that
reports live workers. These are software deadlines subject to OS scheduling, not
hard real-time guarantees. Initialization/reference computation precede the thread
deadline but are included in the outer process deadline.

On failure, pending diagnostic reservations can include an unaccepted prefix;
use native `pushed` and the explicit failure accounting for accepted input.
Pending timestamp coverage is not a substitute for queue depth.

## FIFO replay

The controlled replay API accepts `diagnostic={"batch_metrics": True}`:

```python
from pathlib import Path
from controlled_replay import run_trial
from replay_contract import workload

trial = run_trial(Path("/tmp/fifo-batch-diagnostics"), workload("fifo-smoke-v1"),
                  session_id="diagnosis", diagnostic={"batch_metrics": True})
```

Put `benchmarks` on the Python import path, as with the profiling tools. The
worker's `batch_metrics` summarizes the existing native-call clocks; it does not
add a per-record timer or change the prepared-chunk execution boundary. The
histogram update remains inside the full harness. No estimated probe cost is
subtracted. Diagnostic trials are explicitly ineligible for v1 qualification.

Every replay supervisor result now includes a shutdown outcome, return code,
forced-kill flag, process-group kill flag, observed cleanup time and configured cleanup bound. Failed
trials retain the last protocol phase and last reported processed count; that
count is partial progress, not an inferred final portfolio. The existing parent
watchdog applies run/progress/batch/RSS limits and escalates SIGTERM to SIGKILL.
`forced_kill` means the leading worker required SIGKILL after the TERM grace;
`process_group_kill_sent` records the final SIGKILL used to contain helpers. A
helper retaining stdout after its parent exits is still subject to the watchdog
and group termination.
Normal exits have zero intervention time; that does not claim zero process-exit
latency. Report persistence and whole-harness timers retain their prior meanings.

Disable `diagnostics`/`batch_metrics` to restore the uninstrumented path. See the
[LAT-08 report](../performance/batch_diagnostics_2026-09-20.md) for measured overhead
and the decision on diagnostic use. No logger dependency or per-record logging is
introduced.
