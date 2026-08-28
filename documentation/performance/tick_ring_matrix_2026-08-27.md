# Tick ring parameter matrix — 2026-08-27

This development-only benchmark compares the C++ SPSC ring's in-place factor
processor with the batched Python/NumPy bounded-queue baseline. It is evidence
for choosing experiments, not a portable performance guarantee.

## Environment and workload

- Apple Silicon, macOS 15.5, Python 3.12.14, NumPy 2.5.2
- 100,000 synthetic 64-byte tick records per trial
- Three measured repetitions after one warmup
- Ring capacity: 4,096 records
- Batch sizes: 64, 256, and 1,024
- Spin budgets: 0, 64, 256, and 1,024 attempts before parking

## Results

| Batch | Spins | C++ in-place M ticks/s | Python batch M ticks/s | Ratio | C++ CPU/wall |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 64 | 0 | 9.94 | 9.45 | 1.05 | 1.30 |
| 64 | 64 | 10.29 | 9.56 | 1.08 | 1.32 |
| 64 | 256 | 10.02 | 9.22 | 1.09 | 1.31 |
| 64 | 1,024 | 10.84 | 9.28 | 1.17 | 1.32 |
| 256 | 0 | 30.13 | 28.62 | 1.05 | 1.41 |
| 256 | 64 | 31.11 | 28.28 | 1.10 | 1.44 |
| 256 | 256 | 30.96 | 28.90 | 1.07 | 1.45 |
| 256 | 1,024 | 33.14 | 28.54 | 1.16 | 1.51 |
| 1,024 | 0 | 37.06 | 51.55 | 0.72 | 1.55 |
| 1,024 | 64 | 48.39 | 51.36 | 0.94 | 1.51 |
| 1,024 | 256 | 44.86 | 50.88 | 0.88 | 1.61 |
| 1,024 | 1,024 | 44.92 | 50.03 | 0.90 | 1.77 |

All trials reported zero sequence errors and zero rejected pushes.

## Decision

- Continue the native path for tick-sized and modest-batch ingestion research.
- Keep large research batches in vectorized NumPy/Polars; the ring adds no value
  when batch transfer and vectorized calculation already dominate.
- Default to a modest bounded spin followed by parking. A 1,024-spin budget did
  not produce a stable enough gain to justify its higher CPU demand.
- Do not set a CI throughput threshold from this short, cache-hot laptop run.
  CI should enforce correctness; longer pinned-core runs should establish
  performance regression bands on controlled hardware.

## Reproduction

```bash
.venv/bin/python benchmarks/tick_ring_benchmark.py \
  --matrix --ticks 100000 \
  --batch-sizes 64 256 1024 \
  --capacities 4096 \
  --spin-counts 0 64 256 1024 \
  --repeats 3 --warmups 1 \
  --output /tmp/gambit-tick-matrix.json
```
