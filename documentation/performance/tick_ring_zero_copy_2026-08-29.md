# Tick-ring zero-copy NumPy baseline — 2026-08-29

This development-only run evaluates the experimental leased NumPy ring view. It
is not a portable performance guarantee or a CI threshold.

## Environment and workload

- Apple Silicon, macOS 15.5, Python 3.10.20, NumPy 2.2.6
- 100,000 synthetic 64-byte ticks per trial
- Five repetitions after one warmup
- Ring capacity 65,536; spin budget 256; 1 ms park timeout; no backoff

## Median throughput

| Batch | Python queue/tick | Python batch | Native copy | Native zero-copy | C++ in-place |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 64 | 1.21M/s | 9.76M/s | 10.59M/s | 8.32M/s | 15.59M/s |
| 256 | 1.20M/s | 32.88M/s | 17.88M/s | 17.26M/s | 25.10M/s |
| 1,024 | 1.21M/s | 70.42M/s | 17.01M/s | 33.57M/s | 37.51M/s |

All measured trials reported zero sequence errors and rejected pushes.

## Decision

- Keep the leased NumPy view experimental. Per-lease Python and NumPy overhead
  dominates at small batches.
- Zero-copy is useful evidence at batch 1,024, where it nearly doubles copied-ring
  throughput, but in-place C++ and ordinary batched NumPy remain faster.
- Do not add Arrow/Polars adapters until a representative workload demonstrates
  value beyond direct NumPy batching.

## Reproduction

```bash
.venv/bin/python benchmarks/tick_ring_benchmark.py \
  --matrix --ticks 100000 \
  --batch-sizes 64 256 1024 --capacities 65536 \
  --spin-counts 256 --park-timeouts 0.001 \
  --backoff-counts 0 --maximum-backoff 0 \
  --repeats 5 --warmups 1 \
  --output /tmp/gambit-zero-copy-matrix.json
```
