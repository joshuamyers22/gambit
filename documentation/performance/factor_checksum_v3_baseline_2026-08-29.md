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

## End-to-end DAG and calibration follow-up

The store-backed three-node DAG was rerun after v3 became the publication default:

| Rows | Recompute | V3 cold publish | V3 warm DAG hit | Warm speedup |
| ---: | ---: | ---: | ---: | ---: |
| 1M | 23.80 ms | 90.11 ms | 11.84 ms | 2.01× |
| 10M | 245.21 ms | 804.51 ms | 99.25 ms | 2.47× |

Native calibration over 1 MiB and 16 MiB samples estimated 6.59 GiB/s verified
reads, 2.14 GiB/s durable writes, zero fitted read fixed cost, and 0.250 ms fitted
write fixed cost. Page-cache eviction advice is unavailable on this host, so these
are warm-cache/APFS figures rather than physical NVMe limits.

Defaults are deliberately lower: 2 GiB/s reads, 1 GiB/s writes, 0.250 ms fixed
reads, and 0.500 ms fixed writes. The end-to-end fixture remains declined at two
expected uses because cold publication plus one hit is slower than two cheap
recomputations. Users targeting a specific cache device should continue to build
a policy from an on-device calibration instead of assuming these defaults are a
device guarantee.

With the recalibrated defaults, cost-aware execution took 32.00 ms at 1M rows and
272.81 ms at 10M rows, declined all three nodes, and reused persistent rejection
hints. The forced warm-hit timings show potential reuse speed; the admission result
correctly accounts for the cold write that forced-cache comparisons omit.
