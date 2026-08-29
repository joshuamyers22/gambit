# Factor-cache 1M/10M scale baseline — 2026-08-29

This isolated run used the macOS development host's internal APFS filesystem,
three repetitions, a three-node/seven-column Polars factor DAG, and exact
cross-format equality checks:

```bash
.venv/bin/python benchmarks/factor_cache_benchmark.py \
  --rows 1000000 10000000 --repeats 3 \
  --cache-directory /Users/jkm0607/gambit-benchmark-20260829
```

| Rows | Recompute | IPC mmap | Native reopen | Native resident | Native DAG hit |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1M | 24.09 ms | 4.03 ms | 54.46 ms | 3.87 ms | 58.84 ms |
| 10M | 244.33 ms | 40.61 ms | 592.79 ms | 9.49 ms | 588.05 ms |

IPC mmap reuse was 5.98× and 6.02× faster than recomputation. Forced native DAG
reuse was only 0.41× and 0.42× as fast as recomputation. The cost-aware policy
declined all three cheap nodes and persistent rejection hints avoided pointless
cache-open attempts on later runs.

The native resident view itself was fast. At 10M rows it traversed the data in
9.49 ms, versus 592.79 ms for reopen plus first access. Code inspection confirms
that v2 first access validates every 256 KiB chunk using serial FNV-1a; this
integrity scan dominates the reopen path. The benchmark now reports resident
speedup and the estimated reopen fraction attributable to validation so future
checksum experiments can be compared directly.

## Storage

At 10M rows, the 560 MB logical factor result used approximately 560.1 MB for IPC,
560.1 MB for native columns, and 512.8 MB for Parquet. Native filesystem allocation
amplification was 1.00009×. The entire isolated artifact directory occupied 2.8 GB
because the benchmark intentionally materializes several formats and DAG variants.

## Device telemetry limitation

The host is macOS, so Linux sysfs cumulative sector counters were unavailable.
Physical device writes, controller/NAND write amplification, and NVMe SMART wear
were not measured. Host-visible file allocation is not a substitute for those
values. A Linux run on an otherwise idle target device is still required.

## Decision

- Keep Polars IPC mmap as the default full-column cache representation.
- Keep the native format experimental and do not weaken integrity validation merely
  to improve benchmark results.
- Benchmark a versioned faster chunk checksum (for example, a portable high-speed
  non-cryptographic checksum) against corruption fixtures before changing format.
- Retain native resident views for selective access and C++ tick processing, where
  mappings can remain leased and validation cost can be amortized.
