# Tick ring baseline — 2026-08-27

Environment: Apple silicon, macOS 15.5, Python 3.12.14, NumPy 2.5.2.
Workload: one million ordered 64-byte tick records, capacity 65,536, native and
Python batch size 1,024.

| Pipeline | Throughput | Wall time | Sequence errors | Rejected pushes |
| --- | ---: | ---: | ---: | ---: |
| Python bounded queue, one sequence per item | 1.59M ticks/s | 627.59 ms | 0 | 0 |
| Python bounded queue, NumPy batch views | 154.71M ticks/s | 6.46 ms | 0 | 0 |
| Native SPSC ring, copied batches | 15.65M ticks/s | 63.89 ms | 0 | 0 |

The native ring recorded 1,232 bounded spin attempts and one park in this run.

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

Do not integrate the current copied native ring into the backtest engine. Use
batched handoff as the reference design. Continue native work only if the consumer
runs inside C++ against ring slots in place, or if leased zero-copy batches remove
the second copy and an end-to-end tick workload demonstrates lower tail latency.

The ring remains useful as a correctness and architecture prototype: fixed record
layout, release/acquire publication, cache-line-separated cursors, explicit overflow
accounting, bounded spinning, GIL-free waits, and OS parking are now executable and
tested.

Reproduce with:

```console
.venv/bin/python benchmarks/tick_ring_benchmark.py \
  --ticks 1000000 --batch-size 1024 --capacity 65536
```
