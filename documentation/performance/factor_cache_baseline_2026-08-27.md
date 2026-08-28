# Factor cache baseline — 2026-08-27

Environment: Apple silicon, macOS 15.5, Python 3.12.14, Polars 1.44.1,
NumPy 2.5.2, APFS-backed `/private/tmp`. Results are medians from warm/page-cache
runs and are not claims about every NVMe device.

The benchmark now reports exact equality for every representation, filesystem
allocated bytes, file-size/allocation amplification, cache device and block-size
metadata, and advisory page-cache-eviction reads on platforms that expose
`posix_fadvise`. The eviction call is a kernel hint, not proof that a physical
NVMe read occurred. Device-controller and NAND-level write amplification cannot
be inferred from file sizes and is explicitly marked unmeasured.

The workload is a branching seven-column factor DAG with shared return and moving-
window ancestors. All read measurements touch and aggregate every output column.

## Results

| Operation | 1M rows / 56 MB | 10M rows / 560 MB |
| --- | ---: | ---: |
| Recompute Polars factor DAG | 23.18 ms | 247.02 ms |
| Polars IPC write | 9.96 ms | 105.92 ms |
| Polars IPC map/reopen + read | 3.60 ms | 39.45 ms |
| Polars IPC resident read | 0.84 ms | 8.74 ms |
| Parquet write | 42.26 ms | 354.38 ms |
| Parquet read | 11.18 ms | 89.74 ms |
| Raw NumPy mmap write | 210.99 ms | 692.01 ms |
| Raw NumPy mmap reopen + read | 5.61 ms | 61.16 ms |
| Native committed mmap write | 93.40 ms | 1067.19 ms |
| Native committed mmap reopen + verify + read | 57.86 ms | 601.44 ms |
| Native committed mmap resident read | 0.81 ms | 8.82 ms |

## Adversarial interpretation

- Mapping reusable factor columns is worthwhile: IPC map/read is materially faster
  than recomputing this DAG at both sizes.
- The current native resident view does not materially outperform a resident Polars
  IPC frame. The small 1M difference reverses at 10M and is within benchmark noise.
- The native two-phase/checksummed writer is substantially slower than Polars IPC.
- Verifying a whole-column FNV checksum every time a native segment is opened makes
  reopen performance substantially worse than IPC and raw mapping.
- A descriptor ring would coordinate publication but would not remove checksum,
  flush, page-fault, or storage costs. It is therefore not justified by this result.
- Parquet remains useful for compact durable storage, but IPC is the stronger warm
  factor-cache baseline for these fixed-width columns.

## Decision

Use memory-mapped Polars IPC as the first production-oriented factor-cache baseline.
Retain the native mapped-column primitive as an experimental correctness fixture.
Do not implement the spin/ring path until a workload demonstrates a bottleneck in
descriptor coordination that IPC plus a simple bounded queue cannot satisfy.

Any future native experiment must avoid unconditional whole-column verification on
every open. Candidate designs include a crash-safe manifest with verification at
publication, chunk/page hashes verified lazily, and trusted resident leases. Those
choices require a separate threat and durability analysis.

## V2 selective-verification follow-up

The chunked v2 prototype retains eager v1 compatibility and uses 256 KiB chunks
that grow automatically when the header table would overflow. On the same 1M-row,
seven-column workload, a warm reopen plus verified 65,536-row prefix read measured
3.58 ms, compared with 22.14 ms for DAG recomputation and 4.08 ms for IPC mmap/read.
The earlier 4 MiB chunk prototype took 27.8 ms for the same prefix and was rejected
as too coarse.

A complete v2 reopen and `.values` verification still measured 54.78 ms because it
hashes every byte, so this does not reverse the production preference for IPC on
full-column scans. V2 is promising specifically when a factor-tree branch touches
small column ranges. These are short, warm local measurements rather than portable
NVMe guarantees.

The benchmark is reproducible with:

```console
.venv/bin/python benchmarks/factor_cache_benchmark.py \
  --rows 1000000 10000000 --repeats 3 --cache-directory /path/on/nvme
```
