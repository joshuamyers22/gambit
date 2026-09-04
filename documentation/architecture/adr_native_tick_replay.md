# ADR: Separate experimental historical replay from live tick transport

- Status: accepted for the experimental slice; general-strategy migration deferred.
- Date: 2026-09-04.
- Decision authority: Josh Myers (top-of-book execution selected explicitly).
- Extension: Josh Myers approved conservative FIFO queue-position testing on
  2026-09-04; the opt-in contract is in `fifo_queue_execution.md`.
- Reference: production-project-template `e132c6e`, C++ guide and ADR template.

## Context

The user wants two to three years of multi-instrument tick-level backtesting in
a few seconds after preprocessing. The existing native benchmark measures factors,
not order execution and accounting. A follow-up split timing found ring insertion
dominated that factor path. The actual general Strategy engine still has Python
callbacks and per-timestamp containers.

## Options

1. Optimize the live ring and leave strategy/accounting in Python: preserves the
   existing architecture but does not address the end-to-end requirement.
2. Replace all Strategy internals: too broad before establishing an executable
   policy reference and representative workload.
3. Add a direct native scan and a separately named characterization backtester:
   establishes evidence without silently changing existing order semantics.

## Decision

Choose option 3. Keep existing ring APIs and Strategy behavior. Add direct tick
factor batches that share existing arithmetic. Add an experimental, long-only,
market-order top-of-book backtester with a compiled alternating-target strategy.

Use compact instrument-indexed state, bounded pre-reserved order/fill audit
buffers, and a single owner for mutable state. A batch-level access guard rejects
concurrent calls rather than serializing indefinitely or permitting races.
Require exact typed/aligned/contiguous input, reject stale or invalid feeds,
check integer overflow, and refuse results from failed runs.

For the new prototype only, use integer price ticks, lots and monetary units
with explicit fee rounding. Keep the existing factor processor's floating-point
semantics unchanged. Use the existing checked-in setuptools build contract and
C++11-compatible implementation; do not migrate unrelated extensions to C++20
or CMake just to copy the template. Modernization remains a separate decision.

## Consequences and verification

- Benefits: bounded-memory replay, isolated policy tests, no live transport in
  historical scans, and no per-event Python objects in the native engine.
- Limits: one active order per instrument, a fixed long-only strategy,
  refreshed top-of-book liquidity in default market mode, no own impact, and no
  claim of compatibility with unsupported existing order or risk policies.
- The opt-in FIFO mode shares accounting and validation but uses a separate
  trade-plus-quote input schema. Its volume-ahead estimate is not observed venue
  priority; do not combine its benchmark with the original market-only results.
- Cash is checked before each candidate fill; an unaffordable candidate rejects
  the remaining order instead of resizing it to available cash.
- The Python oracle must match complete order/fill traces and portfolio state.
  Native input/overflow/capacity cases run under sanitizers as well as ordinary
  tests. Benchmark timing is separate from reference checking and data generation.
- Full-volume synthetic ledger reconciliation is necessary but not sufficient
  proof of general strategy parity. The acceptance brief records remaining work.
- Rollback: stop using the experimental module and return to existing Strategy;
  no persisted production schema, order type, or result format has been migrated.
- Do not promote this API or claim general performance until a real strategy,
  preprocessed data layout, fill policy, and full measurement boundary pass review.
