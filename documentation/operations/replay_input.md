# Replay input experiments

LAT-07 separates synthetic generation from storage reads. It changes no FIFO
execution policy and does not turn synthetic data into a production corpus.
`controlled_replay.py` keeps its original `legacy` input default and timer boundary.
Experimental modes are explicit and cannot qualify under the existing v1 gate.

## Modes and ownership

`fused` fills a bounded reusable buffer in one native pass. The benchmark-only
C++11 library reproduces every byte of `make_queue_events(rate=10)`, including
unsigned mixer wrap and zero reserved fields. The helper checks extents and
timestamp overflow before writing. No runtime package dependency or native FIFO
extension change is needed. Library/source hashes and compiler flags are recorded.

`readinto` reads the manifest-backed raw records into one reusable buffer, handling
short reads explicitly. `mmap` maps one page-aligned window at a time, bounded by
the chunk size plus one mapping-alignment unit. Its page faults may occur while
hashing or processing, so a small mapping-call time is not a load-speed claim.
These inputs already contain fixed-layout records: no CSV or compressed-format
decoding is measured.

All modes retain full input hashing inside the harness, native processing,
materialization, result hashing, reconciliation and persistence. New input setup
time includes buffer allocation, library loading or opening the reader. The
one-million-record disposable warmup remains unchanged for both variants.

The reader returns a read-only array whose descendants retain its lifetime.
Keeping any array, field/slice view or memoryview prevents the next read and close;
the reader raises `BufferError` instead of overwriting or unmapping live data.
The worker discards its array only after hashing and synchronous native processing
finish. Reader calls reject concurrent access. Read-only flags are a cooperation
contract; callers must not bypass them or mutate the backing dataset.

Storage uses the existing manifest/schema/length validation and full digest check.
Opened-file length is checked before reads and at close. Mapped files must remain
immutable: truncating an already mapped page externally can fault a process.
The controlled runner confines native faults to its supervised worker and retains
failure evidence. A failed run supplies no reusable result.

## Build and measure

Build the ordinary project first, then build the optional input helper into a new
directory outside the repository:

```sh
.venv/bin/python benchmarks/replay_input.py --output /tmp/gambit-input
.venv/bin/python benchmarks/input_comparison.py \
  --scenario fifo-3y-sparse-v1 --modes legacy fused \
  --library /tmp/gambit-input/queue_input.dylib \
  --pairs 30 --output /tmp/gambit-input-primary
```

Linux builds produce `queue_input.so`. `--sanitize` builds with ASan/UBSan for
correctness checks only; launch Python with the matching sanitizer runtime
preloaded. The recorded macOS checks use a 16 MiB ASan quarantine to stay within
the ordinary supervised worker RSS limit. To run a single experimental trial, pass `--input-mode
fused --input-library PATH` to `controlled_replay.py run`; `--input-mode legacy`
is the fallback. A library built from another source digest is rejected.

Prepare and compare a bounded synthetic storage corpus separately:

```sh
.venv/bin/python benchmarks/controlled_replay.py prepare \
  --scenario fifo-1m-dense-v1 --output-dir /tmp/gambit-input-corpus
.venv/bin/python benchmarks/input_comparison.py \
  --scenario fifo-1m-dense-v1 --modes legacy readinto mmap \
  --dataset /tmp/gambit-input-corpus --warm-cache \
  --pairs 30 --output /tmp/gambit-input-storage
```

`--warm-cache` performs a full sequential pre-read before each worker launch and
records the method. It does not attest OS page residency. Without that option,
cache state is **uncontrolled**, including the first read of a newly written file.
Neither method establishes cold-cache evidence. Do not call a run cold without
external cache-control evidence; no global purge or cache eviction is performed.

The comparison cycles build/mode permutations and requires identical workload,
host, native FIFO binary, source content and input/result hashes. It screens the
full harness, not generation or mapping-call time alone: at least 10% paired median
gain, bootstrap uncertainty excluding zero, and no unexplained >5% p95 execution,
CPU or RSS increase. Raw p50/p95/p99/max and all failures remain available.
Thirty observations are development screening; absolute storage SLAs, real-data
decode behavior and production qualification remain separate work.
