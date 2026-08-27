# Tick ring baseline — 2026-08-27

Environment: Apple silicon, macOS 15.5, Python 3.12.14, NumPy 2.5.2.
Workload: one million ordered 64-byte tick records, capacity 65,536, native and
Python batch size 1,024.

| Pipeline | Throughput | Wall time | Sequence errors | Rejected pushes |
| --- | ---: | ---: | ---: | ---: |
| Python bounded queue, one sequence per item | 1.59M ticks/s | 627.59 ms | 0 | 0 |
| Python bounded queue, NumPy batch views, transport only | 154.71M ticks/s | 6.46 ms | 0 | 0 |
| Native SPSC ring, copied batches, transport only | 15.65M ticks/s | 63.89 ms | 0 | 0 |

The native ring recorded 1,232 bounded spin attempts and one park in this run.

## In-place factor-processing follow-up

The next run added identical sequence validation plus mid, spread, notional, and
absolute-return calculations. Python used vectorized NumPy operations on batch
views; C++ processed ring slots individually without copying them out.

| Pipeline with factors | Throughput | Wall time | CPU time |
| --- | ---: | ---: | ---: |
| Python batch queue + vectorized factors | 54.76M ticks/s | 18.26 ms | 20.82 ms |
| Native ring + in-place incremental factors | 39.30M ticks/s | 25.45 ms | 41.38 ms |

The in-place native consumer is about 2.5× faster than copying native batches back
to Python, confirming that copy elimination matters. It still trails vectorized
NumPy by about 28% on this synthetic single-instrument workload and consumes more
CPU. The native run recorded 107,563 spin attempts and 215 parks, indicating that
wait-policy tuning is material.

## Interpretation

- The native ring materially improves on crossing the Python queue once per tick.
- It does not improve on passing pre-existing NumPy batch views through a bounded
  Python queue. The Python batch path transfers references; the native prototype
  copies each tick into and back out of ring storage.
- The result does not show that Python processing can execute each tick at 154M/s.
  It isolates transport and sequence validation, not factor or strategy work.
- The current benchmark does not yet measure event p50/p99/p99.9 latency, burst
  overflow, multiple instruments, or persistence lag.

## Decision

Do not integrate either native path into the backtest engine yet. The in-place C++
design is now viable enough for multi-instrument, burst, and latency experiments,
but batched handoff remains the throughput reference. Adoption requires an
end-to-end workload or tail-latency result that offsets its additional complexity
and CPU use.

The ring remains useful as a correctness and architecture prototype: fixed record
layout, release/acquire publication, cache-line-separated cursors, explicit overflow
accounting, bounded spinning, GIL-free waits, and OS parking are now executable and
tested.

Reproduce with:

```console
.venv/bin/python benchmarks/tick_ring_benchmark.py \
  --ticks 1000000 --batch-size 1024 --capacity 65536
```
