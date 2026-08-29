# Factor migration baseline — 2026-08-29

This benchmark isolates one indexed v2 generation, the verified v3 rewrite, and
subsequent legacy collection. It uses three float64 columns on the macOS/APFS
development host:

```bash
.venv/bin/python benchmarks/factor_migration_benchmark.py \
  --rows 1000000 10000000 --columns 3 \
  --cache-directory /path/on/cache-device
```

| Rows | Logical bytes | Migration wall | CPU | Temporary allocation | Host allocation amplification |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1M | 24.0 MB | 48.05 ms | 43.75 ms | 24,027,136 B | 1.00113× |
| 10M | 240.0 MB | 467.24 ms | 425.68 ms | 240,025,600 B | 1.00011× |

Both runs preserved exact values, opened only v3 segments after pointer switching,
and collected exactly the old v2 generation. Peak allocation was approximately
twice the source generation while both immutable copies existed. After collection,
allocation returned exactly to its pre-migration value.

The host exposed no Linux sysfs block counter, so device bytes written and physical
write amplification are `null`. The benchmark captures before/after device
snapshots and will calculate those values automatically when run on a Linux cache
device that exposes a stable backing-block statistic. The counter is whole-device,
so a valid experiment still requires an otherwise idle device.

The measured allocation confirms that the planner's block-allocation estimate is
conservative for these fixtures. It does not remove the need for a free-space
reserve: old leased generations remain present until readers close and garbage
collection is explicitly applied.
