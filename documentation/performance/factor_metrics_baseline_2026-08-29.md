# Factor-cache metrics overhead baseline — 2026-08-29

This is a local development baseline, not a cross-device performance claim. It
was run on macOS from the project virtual environment:

```bash
.venv/bin/python benchmarks/factor_metrics_benchmark.py --samples 100 --workers 4
```

| Operation | Median | p99 | Maximum |
| --- | ---: | ---: | ---: |
| Atomic lifetime record | 199.0 µs | 312.1 µs | 322.9 µs |
| Locked lifetime read | 49.2 µs | 55.6 µs | 58.7 µs |
| Locked read plus OpenMetrics render | 51.2 µs | 66.8 µs | 89.3 µs |

Four spawned writers each performed 100 updates in 1.402 seconds, including
process startup. The final hit count was exactly 500: 100 measured single-process
updates plus 400 contended updates, so no increments were lost.

The atomic record cost is intentionally paid once per DAG run, not once per node
or tick. Benchmark results vary with filesystem durability behavior and must be
rerun on the intended NVMe device before setting a performance budget.
