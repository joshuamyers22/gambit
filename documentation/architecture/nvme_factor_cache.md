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
