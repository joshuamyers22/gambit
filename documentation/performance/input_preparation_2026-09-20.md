# LAT-07 bounded input preparation

Date: 2026-09-20. LAT-07 is **complete for engineering acceptance**. Scope: the approved synthetic FIFO workload and
bounded raw-record storage controls. **Retain the fused synthetic generator as an
explicit experimental input mode; retain the original storage loader.** No FIFO
execution or ring change is made. This does not establish production decode,
cold-storage or latency qualification.

Thirty full primary pairs reduced median harness time from **57.164 to 33.004 s**.
The median paired reduction was **42.32%**, with a bootstrap 95% interval of
**42.11–42.54%**. Peak RSS fell by a paired median **20.09%**. Input and result
hashes matched in every measured trial. The original `legacy` mode remains the
default and fallback; the optimization is selected explicitly with `--input-mode
fused --input-library PATH`.

## Change and safety boundary

The [fused generator](../../benchmarks/fused_queue_input.cpp) reproduces the
88-byte queue record in one C++11 pass, replacing repeated NumPy generation passes
and strided field assignments. Unsigned mixer wrap, integer timestamps, eight
round-robin instruments, prices, sizes, aggressor and zero reserved fields match
the original generator. Extents and timestamp overflow are checked before writing.
There is no allocation inside the native generation loop.

The [reader](../../benchmarks/replay_input.py) owns one reusable buffer, bounded by
the admitted chunk size (5,765,848 bytes at 65,521 records; at most 88 MiB).
Returned arrays are read-only, and a surviving array or descendant view prevents
reuse and close. Calls reject concurrent access. The controlled worker releases
its view only after hashing and synchronous native processing finish. Read-only
flags assume a cooperating caller, as does the original prepared-array API.

The helper is a separately built **benchmark-only** library, not a new installed
package dependency. The existing native FIFO extension remains byte-identical:
`9bb6074a0149a302edd4a1f8ab3063f3ae830b15418342738dcacfde450ffb39`.
Generator source SHA-256:
`81dd4a986892061b229a9372f5c28001da7785db9b5e30a3933dfa2415a2cb85`;
measured helper SHA-256:
`9f4e3a5d12c687f501f1e5513613705ddd3d78aec4bfbfc5dfd46b6c220aa7bc`.
The C++11/O3 command and compiler identity are retained in the build manifest.

## Primary method and distributions

The [comparison driver](../../benchmarks/input_comparison.py) reuses LAT-02's
isolated workers, deadlines, cancellation and failure reporting. Baseline/candidate
order alternates across 30 pairs. Both replay all **946,944,000 events**, with the
same seed, 65,521-record chunks, native binary, execution configuration,
one-million-record disposable warmup, probes, input hashing, reconciliation,
result hashing and persistence. Buffer/library/reader setup is inside the harness.
The existing prepared-chunk execution timer is unchanged. No stage percentiles are
added to derive completion time.

Host: Apple M4, 10 logical CPUs, 24 GiB, macOS 15.5/24F74, Python 3.10.20,
NumPy 2.2.6, Apple Clang 17 (`clang-1700.0.13.5`). Runs were serial, without
concurrent builds/tests; `caffeinate -i` prevented idle sleep. Raw power/load
observations are retained. The thermal query returned errors rather than useful
temperature/performance evidence. This was not an attested quiet or independent
multi-session qualification campaign. Later slow observations are retained.

Quantiles use nearest rank; with 30 samples, p99 is the maximum and is descriptive.
Intervals use 10,000 paired bootstrap resamples, seed 20260920. Three preliminary
100-million-event pairs are retained separately and excluded from these results.

| Metric | Baseline p50 | Fused p50 | Baseline p95 | Fused p95 | Baseline max | Fused max |
|---|---:|---:|---:|---:|---:|---:|
| Full harness, seconds | 57.164 | 33.004 | 60.553 | 35.323 | 65.990 | 35.358 |
| Prepared execution, seconds | 4.191 | 4.158 | 4.420 | 4.449 | 4.708 | 4.557 |
| Process CPU, seconds | 57.118 | 32.965 | 60.303 | 35.130 | 65.270 | 35.223 |
| Peak RSS, bytes | 211,632,128 | 170,885,120 | 239,353,856 | 185,008,128 | 242,384,896 | 187,138,048 |

The primary passes the declared full-harness retention screen. Native execution
p95 changed by +0.65%, within the 5% regression guard. The first pair's diagnostic
generation time fell from 24.408 to 1.688 s, while input hashing stayed near 25 s.
Hashing therefore dominates remaining completion time. The result is not achieved
by omitting validation or reporting generation time as total completion.

## Dense, chunk and storage controls

| Synthetic control, 30 pairs each | Median paired harness reduction | Observation |
|---|---:|---|
| Full 1-million-event dense | 8.56% | p95 execution/CPU/RSS guards passed |
| Full 2-million-event dense | 8.51% | p95 execution/CPU/RSS guards passed |
| 10-million-event prefix, chunks 65,536 | 40.44% | Short execution p95 rose 7.55%; investigated below |
| 10-million-event prefix, chunks 1,048,576 | 56.55% | p95 execution/CPU/RSS guards passed |

The short 65,536-chunk execution medians were 44.050 and 44.144 ms; its paired
median interval included no change, but p95 rose from 47.770 to 51.377 ms. A fresh
30-pair replication retained a 40.38% harness gain and did **not** reproduce the
tail regression: execution p95 was 49.449 → 46.764 ms (−5.43%). The original result
is retained, not discarded or pooled with another workload. A further 30 pairs
at 100 million events and the same chunk size found 41.97% lower harness time;
execution p95 was 451.638 → 463.762 ms (+2.68%, inside the guard). Execution median
intervals include no change in both follow-ups. The original short-tail excursion
was not reproduced; a specific hardware cause is not established. The full primary
and the longer control remain within the native-execution guard.

Storage fixtures contained 1 million/2 million canonical records (88/176 MB),
already decoded into the fixed record layout. Each workload had one uncontrolled
observation per mode and 30 warm-prepared observations per mode, cycling all six
orders of `legacy`, `readinto` and `mmap`. Warm preparation was a full sequential
pre-read before worker launch; OS page residency was not attested. **No cold-cache
measurement is claimed**, including for a newly written file's first read.

| Warm-prepared storage | Buffered reuse: paired harness reduction | Windowed mapping: paired harness reduction |
|---|---:|---:|
| 1 million records | 1.43% [0.26%, 1.93%] | −1.76% [−2.62%, −0.03%] |
| 2 million records | 0.98% [−0.13%, 1.40%] | −0.31% [−2.02%, 0.61%] |

Neither storage alternative clears the 10% end-to-end threshold. In the 1-million
control, peak-RSS p95 rose 7.65% for buffered reuse and 8.38% for mapping; mapped
execution p95 rose 17.35%. They are retained only as explicit comparison tools,
not preferred loaders. The reusable buffer remains allocated until reader
destruction and is included in observed RSS. Mapping faults can occur during
hashing/processing, so a faster mapping call alone is not a loading improvement.
There is no CSV/compression decoding result to generalize from these fixtures.

Manifest/schema/layout/length validation and the full file digest remain mandatory.
Opened-file length is checked before reads and at close. Mapping windows are
bounded by one chunk plus one mapping-alignment unit. Files must remain immutable;
external truncation of an active mapping can fault the isolated worker. Failed
workers cannot supply successful results.

## Validation, evidence and next work

Tests cover byte parity at seed/sequence/timestamp boundaries, maximum chunks and
short final chunks, buffer reuse, derived-view lifetime, concurrent access,
truncation/checksum failures and preservation of native validation errors during
cleanup. Sanitized checks use the same helper source with ASan/UBSan. The default
ASan quarantine initially exceeded the runner's 512 MiB resource limit; the
recorded repeat uses a 16 MiB quarantine while retaining that runner limit.
Leak detection is disabled for the Python process; no leak-check result is claimed.
Final test/check counts and log hashes are retained with the evidence.

Final validation passed **2,247 tests**, **28 ASan/UBSan input tests**, native
warnings, lint, typing, coverage policy, financial mutation checks, documentation
and wheel/source-distribution checks (`make check`). The 11 measured campaigns,
including the two follow-ups, contain **606 distinct workers and 65,075,640,000
measured records**; warmups and preliminary trials are separate. Every worker
completed successfully. Every paired input/result control matched, and every
worker used the same native FIFO binary.

Complete input bytes and result controls match the existing canonical workload on
the unchanged native FIFO binary. This preserves the applicability of prior
LAT-04/05 trace evidence; it is not a claim that a new independent trace campaign
or production qualification was performed. The runner explicitly marks non-legacy
input pipelines ineligible for v1 qualification pending separate review.

The [runbook](../operations/replay_input.md) gives build, replay, storage and
rollback commands. [Compact evidence](input_preparation_2026-09-20/) contains raw
trial archives, summaries, manifests, build records and validation logs. Full
corpora and build artifacts remain under
`/Users/jkm0607/Projects/gambit-evidence/lat07-2026-09-20`, with content hashes.
Allocation counts and hardware performance counters were not collected.

Next: **LAT-08—batch-level metrics, queue high-water/full/age signals, failure
summaries and bounded shutdown reporting**. Real-data decode/storage characterization
and LAT-09 qualification remain open.
