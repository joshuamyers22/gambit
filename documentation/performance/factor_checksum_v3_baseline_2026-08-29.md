# Native factor checksum v3 baseline — 2026-08-29

Format v3 replaces serial FNV-1a chunk checksums with portable XXH64 while
retaining the v2 256 KiB chunk table, lazy touched-range validation, immutable
publication, and read compatibility for v1/v2 segments. The standard XXH64 empty
input vector is tested to prevent implementation drift.

The side-by-side APFS benchmark used seven float64 columns, exact equality checks,
and three read repetitions:

| Rows | v2 write | v3 write | v2 reopen | v3 reopen | v3/v2 reopen |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1M | 138.59 ms | 40.63 ms | 54.79 ms | 6.24 ms | 8.78× |
| 10M | 1.583 s | 599.66 ms | 594.73 ms | 108.10 ms | 5.50× |

At 10M rows, v3 verified reuse was 2.25× faster than recomputation, compared with
0.41× for v2. V3 used the same stored and allocated bytes as v2. It still trailed
Polars IPC mmap for full-frame reuse, so IPC remains that workload's baseline.

V3 detects corruption in touched chunks and does not bypass validation. New factor
store generations use v3; old generations remain readable. The legacy
`create_chunked` entry point continues to produce v2 for compatibility and direct
comparison, while `create_chunked_v3` is explicit about its format contract.

These are warm-page-cache measurements on one Apple Silicon host. CI sanitizer,
thread-sanitizer, fault-injection, and cross-platform jobs remain release gates.
