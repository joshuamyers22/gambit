# NVMe-mapped factor column format v1

Status: experimental; not a production cache contract.

## Factor-DAG identity

`FactorNodeIdentity` defines the cache invalidation boundary independently of the
physical segment format. Its SHA-256 key covers a domain-separated, versioned,
canonical JSON payload containing:

- ordered parent node keys;
- named source-data fingerprints;
- transform name and independently versioned implementation contract;
- recursively normalized parameters;
- ordered output names, physical dtypes, and nullability;
- explicit row-ordering fields; and
- research context such as calendar and floating-point policy.

Mapping keys are sorted, while parent, schema, and row order remain significant.
NaN, infinity, sets, arbitrary Python objects, malformed digests, duplicate output
names, and unspecified ordering are rejected rather than stringified. The canonical
payload is sealed during construction so mutating a caller-owned parameter mapping
cannot change an existing identity. Changing any identity dimension invalidates
the node. The earlier `factor_node_key` string helper remains available only for
compatibility with initial experiments; new DAG integration uses the strict type.

## Identity index and cache reuse

Strict nodes are published through `publish_factor_node`. The canonical identity
snapshot is embedded in the generation manifest, and `nodes/<node-key>` atomically
maps the identity to its immutable generation. `open_generation_by_node_key`
validates the pointer, manifest, reconstructed identity hash, segment metadata,
and lease before returning columns. A missing key is a cache miss; malformed,
symlinked, dangling, or hash-inconsistent state is corruption and fails closed.

The writer-lifecycle lock makes same-node publication a check-then-create critical
section. Concurrent processes therefore converge on the first valid generation;
later publishers validate and reuse it without rewriting column data. The current
format accepts only identity schemas exactly matching the supplied, non-nullable
`float64` columns.

Ordinary orphan collection retains every indexed generation even when it is not
`CURRENT`, because deleting it would turn a valid cache hit into a dangling index.
Index-aware capacity eviction will be a separate operation that first removes a
node pointer atomically, then reclaims its unleased generation. Invalid index state
aborts collection rather than risking live data.

## Polars DAG execution

`PolarsFactorDagExecutor` consumes explicitly topologically ordered nodes. Each
node receives read-only mappings of only its declared parent outputs. A valid cache
entry becomes a Polars `Float64` frame backed by leased mapped columns; a true
`FactorNodeCacheMiss` runs the node callback, validates its exact schema and null
contract, and publishes the result. Corruption exceptions propagate and are never
converted into misses.

`FactorDagExecution` owns all cache-hit leases and must remain alive while its
frames are used. It provides context-manager and idempotent `close()` cleanup.
Telemetry records exact hit and miss node keys. Changing a node identity changes
the parent key embedded in every descendant identity, invalidating only that node
and its dependent subgraph while unrelated ancestors remain reusable.

`FactorCacheAdmissionPolicy` prevents unconditional NVMe writes. After a miss, the
executor measures callback time and Polars output bytes, then estimates total cost
over the node's declared expected-use horizon:

`compute + publish + (expected uses - 1) × mapped read`

The node is admitted only when that estimate beats repeated recomputation by the
configured minimum speedup. Read/write bandwidth and fixed operation costs are
explicit calibration inputs; invalid or non-finite values fail early. Telemetry
records admitted keys, declined keys, and `(node key, compute seconds, bytes)` for
every computed node. The executor retains an `always()` policy for controlled
experiments and backward-compatible comparisons; cost-aware admission is the
executor default.

Declines are stored as atomic, fsynced advisory records under `admission/`. Each
record binds the node key, admission-policy fingerprint, measurement, decision,
and timestamp. A matching unexpired record skips the known-missing strict cache
open on later executions, but never skips computation or admission re-evaluation.
Malformed, substituted, policy-mismatched, future-dated, and expired records are
ignored. The presence of a node-index entry always overrides a rejection hint, so
another process or policy can publish the node without being masked. Successful
admission or cache reuse clears the obsolete rejection. Hints improve performance
only and are not trusted for factor correctness.

## Bounded eviction

Successful strict-node publication and rate-limited cache hits maintain advisory
access records under `access/`. The default interval is 60 seconds, limiting
metadata write amplification; counts therefore represent persisted access samples,
not an exact request counter. Missing, corrupt, symlinked, or future-dated records
fall back to the generation creation time.

`evict_factor_nodes` measures actual manifest and segment file sizes for every
indexed generation and removes least-recently-used nodes until `max_bytes` and the
optional `max_nodes` limit are met. Eviction holds the writer-lifecycle and
exclusive store locks, validates index/manifest correspondence, unlinks and fsyncs
the node pointer first, then deletes the unleased generation and its advisory
metadata. A crash after unindexing leaves an invisible generation that ordinary
garbage collection can reclaim.

The generation named by `CURRENT`, active leases, symlinked lease directories, and
ambiguous lease files are protected. If protected generations alone exceed the
requested bound, eviction reports `limits_satisfied = false`; it never weakens the
lease contract to force a quota. Byte accounting currently covers immutable
generation files, not filesystem metadata or allocator/controller write
amplification.

## Operations and calibration

`inspect_factor_cache(root)` provides a non-mutating inventory containing actual
and allocated bytes, filesystem capacity/free space, device id, indexed and
unindexed generation counts, staging directories, leases, access samples,
rejection hints, and per-node sizes/rows/current status. Malformed advisory or
index state is returned as structured findings. Device wear is explicitly marked
unmeasured rather than inferred from host-visible file sizes.

`calibrate_factor_cache(root)` creates a uniquely named temporary directory on the
selected filesystem, measures two native v2 segment sizes over repeated fsynced
writes and full verified reads, fits fixed cost plus bytes/second estimates, and
removes all calibration segments in `finally`. When supported, it requests page-
cache eviction with `POSIX_FADV_DONTNEED`; the result records that this is advisory,
not proof of a physical NVMe read. The returned `FactorCacheCalibration` produces
a `FactorCacheAdmissionPolicy` through `admission_policy()`.

Calibration must run on the directory/device used by the research cache and should
be repeated after material hardware, filesystem, encryption, thermal, or kernel
changes. It is intentionally never run during import, testing, or an ordinary
backtest.

The installed `gambit-factor-cache` command exposes these operations with JSON
output:

```text
gambit-factor-cache inventory /nvme/gambit-cache
gambit-factor-cache calibrate /nvme/gambit-cache --repeats 3
gambit-factor-cache collect /nvme/gambit-cache
gambit-factor-cache collect /nvme/gambit-cache --apply
gambit-factor-cache evict /nvme/gambit-cache --max-bytes 250GiB --max-nodes 10000
gambit-factor-cache evict /nvme/gambit-cache --max-bytes 250GiB --max-nodes 10000 --apply
gambit-factor-cache quota /nvme/gambit-cache --max-cache-bytes 500GiB --reserve-free-bytes 50GiB
gambit-factor-cache quota /nvme/gambit-cache --max-cache-bytes 500GiB --reserve-free-bytes 50GiB --apply
```

`collect` and `evict` are dry-run by default and require `--apply` to mutate the
store. Size arguments accept bytes and KiB/MiB/GiB/TiB forms. `--output` writes the
same JSON to a file; operational failures also return JSON and a nonzero exit code.
Collection also removes orphaned access and admission records after 30 days by
default. `--metadata-retention-seconds` adjusts that window; records belonging to
an indexed node are never removed by retention cleanup.

`quota` accounts for allocated filesystem blocks across the whole cache. It
triggers above the high watermark (90% by default) or below the reserved free-space
floor, then evicts unleased LRU nodes toward the low watermark (80% by default).
This hysteresis avoids repeated single-node eviction near the boundary. Current and
leased generations remain protected; `limits_satisfied: false` reports when they
make the requested target impossible.

The initial primitive stores one immutable little-endian `float64` column in a
dedicated file located on an NVMe-backed filesystem. The entire file is mapped;
column bytes begin at the page-aligned offset 4096.

## Header

The first 48 bytes use little-endian fields; the rest of the 4096-byte header is
reserved and zero-filled.

| Field | Type | Meaning |
| --- | --- | --- |
| magic | 8 bytes | `GAMBITFC` |
| version | uint32 | v1 whole-column FNV-1a; v2 chunked FNV-1a; v3 chunked XXH64 |
| state | uint32 | 0 writing, 1 committed |
| row count | uint64 | number of float64 values |
| data offset | uint64 | 4096 |
| data bytes | uint64 | row count × 8 |
| checksum | uint64 | algorithm selected by the format version |

## Publication protocol

1. Create a new path exclusively and size it once.
2. Map read/write and leave state at zero.
3. Write column bytes and checksum.
4. Flush the mapped data and incomplete header.
5. Publish committed state with release ordering and flush the header page.
6. Protect the writer mapping read-only.

Readers map read-only, load state with acquire ordering, validate all bounds, and
verify the checksum before returning a view. V2 and v3 verify only touched 256 KiB
chunks and remember verified chunks for the mapping lifetime. Uncommitted,
truncated, extended, or corrupt segments are rejected. Published files are never
modified in place. New factor-store generations use v3 while v1/v2 remain readable.
The portable admission defaults assume 2 GiB/s verified reads, 1 GiB/s durable
writes, and conservative 0.250/0.500 ms fixed read/write costs. These are policy
floors derived below the measured development-host v3 calibration, not claims
about every device; on-device calibration should replace them for deployment.

`mmap.flush`/`msync` does not by itself provide a complete cross-filesystem power-
loss guarantee. A later manifest design must provide generation recovery and define
directory/file synchronization before this becomes production storage.

## Concurrency boundary

No ring lock or slot claim may cover mapping, page faults, writes, checksum work,
flushes, or validation. A future ring transports only committed descriptors. The
first supported topology will be SPSC with bounded spin/backoff/park behavior.

## Generation publication layer

The experimental `gambit.factor_store` module groups immutable column segments
into crash-safe generations:

1. Create `generations/.staging-<generation>` on the store filesystem.
2. Create and commit every immutable column segment inside the staging directory.
3. Write a canonical manifest containing the generation, factor-node key, column
   filenames, row counts, and checksums; fsync the manifest and directory.
4. Rename the staging directory to `generations/<generation>` and fsync its parent.
5. Write and fsync a temporary pointer, atomically replace `CURRENT`, and fsync the
   store directory.

Readers follow only the strictly validated hexadecimal generation named by
`CURRENT`. They reject traversal, filename substitution, symbolic links, manifest
identity mismatch, metadata mismatch, corrupt segments, and uncommitted segments.

Legacy migration reuses this publication protocol. It copies indexed v1/v2 data
into a staged v3 generation, reopens it to validate checksums and exact values,
then switches pointers. It never mutates or deletes the source generation, so
concurrent leased readers remain valid and garbage collection remains a separate
operation. An expected-generation check rejects concurrent pointer replacement.
Dry-run planning is bounded by node count, estimated temporary writes, filesystem
free space, and an explicit free-space reserve; reruns skip v3 nodes.
Each applied batch atomically replaces a bounded advisory progress checkpoint
after every node. Per-node operational failures do not hide successful pointer
switches or stop later independent nodes. Persistent metrics schema v2 adds fixed
migration node, byte, failure, and conflict counters; v1 counter files are upgraded
losslessly on their next write.

Crashes before step 4 leave ignored staging directories. Crashes between steps 4
and 5 can leave a complete but unreferenced generation, which remains invisible.
After atomic pointer replacement, readers see the new complete generation. Garbage
collection remains a separate, explicitly invoked maintenance operation.

Operational counters are persisted under `metrics/lifetime.json` with a fixed,
versioned schema, atomic replacement, and a cross-process file lock. DAG runs
perform one aggregate update after execution rather than writing on each node or
tick. Per-run node identities and timings remain in `FactorDagTelemetry`; this
keeps lifetime metrics bounded and avoids exposing factor keys as Prometheus
labels. The `metrics` and `health` CLI commands provide monitoring without
mutating cache state. See `documentation/operations/factor_cache_maintenance.md`.
Prometheus export uses stable, label-free counter names; OpenMetrics export adds
the required end-of-document marker. Linux device inspection can report cumulative
512-byte sectors written from sysfs, but deliberately does not equate host writes
with NAND writes, controller amplification, or NVMe SMART percentage-used wear.
Spawned-process fault tests terminate publication after a column write, after the
staging-directory fsync, after the generation-directory fsync, and immediately
after pointer replacement. They assert that reopening yields the complete old
generation at the first three boundaries and the complete new generation at the
last boundary. Publication holds an exclusive writer-lifecycle lock from staging
creation through pointer replacement. Garbage collection acquires that same lock
before removing abandoned `.staging-*` directories and temporary `.CURRENT-*`
pointers, so it cannot delete a generation that a live writer is constructing.
This serializes writers by design; mapped column construction remains outside the
shorter reader/publication lock, so existing readers are not blocked by column I/O.

Readers now take a shared store lock while resolving `CURRENT`, validating and
opening its segments, and durably creating a per-process lease. Publication and
garbage collection take the exclusive lock. A returned `FactorGenerationLease`
must remain alive for as long as any of its column mappings are used and supports
explicit `close()` and context-manager cleanup.

The lease/collection contract is also exercised across independently spawned
processes: garbage collection preserves an old generation while a reader process
holds its lease, then removes it only after that process closes the lease.

Garbage collection removes only non-current generations without leases. A stale
lease may be reclaimed only after the configured age when it names the local host
and its PID is demonstrably absent. Malformed, remote-host, permission-ambiguous,
or otherwise unverifiable leases retain their generation. This intentionally
prefers leaked disk space over use-after-delete risk. PID reuse can delay cleanup
but cannot cause deletion because a reused live PID is treated as active.

The generation layer now publishes v2 segments by default. V1 segments remain
readable with eager whole-column verification, and the publication protocol is
shared by both versions.

## Chunked segment v2

V2 preserves the v1 header prefix and adds `chunk_bytes` and `chunk_count` fields,
followed by a bounded array of FNV-1a-64 chunk hashes within the reserved 4 KiB
header page. Chunk size defaults to 256 KiB and grows automatically when necessary
so the complete table always fits in the header. V1 creation and eager validation
remain available for compatibility.

Opening v2 validates magic, version, commit state, mapping bounds, chunk geometry,
and table bounds without reading all column bytes. `slice(start, stop)` verifies
each touched chunk once per mapped object under a mutex, then returns a read-only
NumPy view. Requesting `.values` verifies every chunk before returning the complete
view. A failed chunk remains unverified and every later access retries and fails.
The generation manifest records the segment version, row count, and whole-column
publication checksum so version or header substitution fails closed.

Lazy verification assumes published files are not modified in place. It prevents
undetected pre-access corruption; it cannot prevent a privileged writer from
changing bytes after verification. Production deployment therefore also requires
filesystem permissions and storage isolation appropriate to the threat model.

## Tick transport prototype

The native extension also contains an in-memory SPSC tick ring. This ring is not
part of the mapped storage format and performs no storage operations. Each 64-byte
record contains sequence and event/receive times, price, quantity, bid, ask,
instrument id, and flags. Capacity is a power of two; producer and consumer cursors
are cache-line separated and use release/acquire publication.

Batch push rejects newest records once full and increments an explicit drop count.
Waiting consumers spin for a bounded number of attempts, periodically yield, then
optionally use exponentially increasing sleeps capped by
`maximum_backoff_seconds`, then park on a condition variable with a timeout. The
backoff phase is opt-in; defaults preserve the earlier spin/yield/park behavior.
`close()` cancels a parked consumer without timeout polling and rejects later
pushes. Producer notifications coordinate with the wait mutex to prevent the
predicate-check/wait lost-wakeup race. Metrics distinguish spins, yields, backoffs,
parks, park timeouts, and successful wakeups. The GIL is released during native
transfer and waiting. This first prototype supports exactly one producer and one
consumer; additional concurrent callers violate its contract.

`benchmarks/tick_ring_benchmark.py --sparse-wait` paces individual arrivals and
compares native wait policies with Python's blocking queue using producer-side
monotonic timestamps. It reports per-tick p50/p99/maximum wake latency and
CPU-to-wall ratio. Initial unpinned local results did not meet the promotion gate
for adaptive backoff, so the default remains no backoff; benchmark output is
machine-specific and is not a portable latency guarantee.

`TickFactorProcessor` is the first in-place consumer. It reads ring slots directly
before advancing the consumer cursor and maintains per-instrument previous prices,
sequence continuity, quantity/notional totals, mid/spread aggregates, absolute
returns, and maximum feed latency. Python receives only an aggregate snapshot; tick
records are not copied out of the ring. This is an experimental processor rather
than the final factor API.

`lease_batch()` and `wait_lease_batch()` expose an experimental read-only NumPy
view over the contiguous consumer tail. Only one consumer lease may exist. Closing
requests release, but the tail cursor advances only after every derived array is
destroyed; each view retains shared lease state that in turn retains the ring's
Python owner. A lease stops at wraparound and the next lease exposes the following
contiguous segment. Competing copied/in-place consumers fail while a lease is
active. The state/view counters are manipulated only while Python holds the GIL.
