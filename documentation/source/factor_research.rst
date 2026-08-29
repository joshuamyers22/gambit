Factor research and the native cache
====================================

Purpose
-------

The factor DAG stores immutable float64 columns in memory-mapped files so later
nodes can reuse earlier work without loading an entire research matrix into
memory. Node identity includes inputs, operation parameters, schema, and lineage;
changing an upstream definition invalidates only affected descendants.

Polars DAG execution
--------------------

Represent each operation as a named node with declared parents. The executor
checks the factor store before computing and publishes a completed generation
atomically. Admission considers computation time, output size, expected reuse,
and calibrated device costs.

Treat cache hits as an optimization only. A cache miss or rejected admission
must not alter numerical results.

NVMe and memory mapping
-----------------------

Memory mapping avoids an extra userspace copy but does not guarantee that data
is resident in RAM or written immediately to physical media. OS page cache,
filesystem allocation, writeback, compression, and the NVMe controller all
affect measurements. Benchmark cold, warm, and resident cases separately.

Tick processing
---------------

``TickRing`` is a bounded single-producer/single-consumer transport implemented
in C++. It supports bounded spinning, parking, cancellation, and optional
zero-copy NumPy leases. The zero-copy lease holds the consumer cursor until the
view is released; retaining it can stall the producer. Copy batches when their
lifetime must escape the processing callback.

Operational safety
------------------

Use the CLI in preview mode first::

   gambit-factor-cache inspect /nvme/gambit-cache
   gambit-factor-cache collect /nvme/gambit-cache

Collection and eviction are dry-run by default. Apply mutations only after
reviewing leased generations and projected reclamation. Do not place the cache
on irreplaceable storage: it is reconstructible research state, not a source of
record.

Benchmarking
------------

Report rows, columns, bytes, filesystem, device, cache state, concurrency,
software revision, and distribution statistics. Throughput without CPU time,
tail latency, allocation amplification, and device-write deltas can hide the
actual bottleneck or shift cost into hardware wear.
