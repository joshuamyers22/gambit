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

Readers now take a shared store lock while resolving `CURRENT`, validating and
opening its segments, and durably creating a per-process lease. Publication and
garbage collection take the exclusive lock. A returned `FactorGenerationLease`
must remain alive for as long as any of its column mappings are used and supports
explicit `close()` and context-manager cleanup.

Garbage collection removes only non-current generations without leases. A stale
lease may be reclaimed only after the configured age when it names the local host
and its PID is demonstrably absent. Malformed, remote-host, permission-ambiguous,
or otherwise unverifiable leases retain their generation. This intentionally
prefers leaked disk space over use-after-delete risk. PID reuse can delay cleanup
but cannot cause deletion because a reused live PID is treated as active.

This layer does not make v1 opens lazy: every segment still receives full checksum
verification. A future v2 segment may add chunk hashes and verified-chunk state,
but v1 compatibility and this publication protocol must remain unchanged.

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
