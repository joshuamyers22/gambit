# NVMe-mapped factor column format v1

Status: experimental; not a production cache contract.

The initial primitive stores one immutable little-endian `float64` column in a
dedicated file located on an NVMe-backed filesystem. The entire file is mapped;
column bytes begin at the page-aligned offset 4096.

## Header

The first 48 bytes use little-endian fields; the rest of the 4096-byte header is
reserved and zero-filled.

| Field | Type | Meaning |
| --- | --- | --- |
| magic | 8 bytes | `GAMBITFC` |
| version | uint32 | format version 1 |
| state | uint32 | 0 writing, 1 committed |
| row count | uint64 | number of float64 values |
| data offset | uint64 | 4096 |
| data bytes | uint64 | row count × 8 |
| checksum | uint64 | FNV-1a-64 over column bytes |

## Publication protocol

1. Create a new path exclusively and size it once.
2. Map read/write and leave state at zero.
3. Write column bytes and checksum.
4. Flush the mapped data and incomplete header.
5. Publish committed state with release ordering and flush the header page.
6. Protect the writer mapping read-only.

Readers map read-only, load state with acquire ordering, validate all bounds, and
verify the checksum before returning a view. Uncommitted, truncated, extended, or
corrupt segments are rejected. Published files are never modified in place.

`mmap.flush`/`msync` does not by itself provide a complete cross-filesystem power-
loss guarantee. A later manifest design must provide generation recovery and define
directory/file synchronization before this becomes production storage.

## Concurrency boundary

No ring lock or slot claim may cover mapping, page faults, writes, checksum work,
flushes, or validation. A future ring transports only committed descriptors. The
first supported topology will be SPSC with bounded spin/backoff/park behavior.

## Generation publication layer

The experimental `gambit.factor_store` module groups immutable v1 column segments
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

Crashes before step 4 leave ignored staging directories. Crashes between steps 4
and 5 can leave a complete but unreferenced generation, which remains invisible.
After atomic pointer replacement, readers see the new complete generation. Garbage
collection remains a separate, explicitly invoked maintenance operation.
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
park on a condition variable with a timeout. The GIL is released during native
transfer and waiting. This first prototype supports exactly one producer and one
consumer; additional concurrent callers violate its contract.

`TickFactorProcessor` is the first in-place consumer. It reads ring slots directly
before advancing the consumer cursor and maintains per-instrument previous prices,
sequence continuity, quantity/notional totals, mid/spread aggregates, absolute
returns, and maximum feed latency. Python receives only an aggregate snapshot; tick
records are not copied out of the ring. This is an experimental processor rather
than the final factor API.
