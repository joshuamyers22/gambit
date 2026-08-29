# Changelog

## Unreleased

- Add atomic, cross-process factor-cache lifetime metrics with bounded Prometheus export.
- Add non-mutating cache health thresholds and operational scheduling guidance.
- Record aggregate DAG admission/reuse decisions and applied eviction reclamation.
- Add label-free OpenMetrics export, observability overhead benchmarks, and conservative Linux device-write telemetry.
- Extend factor-cache decisions with resident-versus-verification measurements and a 1M/10M scale baseline.
- Add backward-compatible native segment v3 with XXH64 chunk validation and use it for new factor-store generations.
- Recalibrate v3 admission defaults and record 1M/10M end-to-end DAG speedups.
- Add bounded, dry-run-first, lease-safe and resumable v1/v2-to-v3 factor migration.
- Add reproducible migration latency, allocation-amplification, and Linux device-delta benchmarking.

This project follows [Semantic Versioning](https://semver.org/). Changes that
have not yet been released are collected below.

## Unreleased

### Added

- Immutable, telemetry-bearing backtest results and verified result bundles.
- Typed risk, stress, validation, provenance, execution-cost, and instrument APIs.
- Experimental native mapped-column and tick-processing prototypes.
- Crash-safe experimental factor generations with reader leases and conservative
  garbage collection.
- Backward-compatible v2 mapped columns with lazy per-chunk slice verification.
- Canonical factor-DAG identities with manifest-backed node lookup, cross-process
  publication deduplication, and index-aware garbage collection.
- A Polars factor-DAG executor with leased mapped-column reuse, cache telemetry,
  and lineage-driven partial invalidation.
- Cost-aware factor-node admission based on measured computation, output size,
  expected reuse, and calibrated mapped read/write cost estimates.
- Expiring, policy-keyed rejection hints that bypass repeated missing-node cache
  opens without masking nodes published by another process.
- Rate-limited access metadata and byte/node-bounded LRU eviction that protects
  current and leased factor generations.
- Read-only cache inventory and on-device native segment calibration for admission
  policy parameters, with explicit page-cache and device-wear limitations.
- A JSON `gambit-factor-cache` operations CLI whose collection and eviction
  commands default to dry-run and require explicit `--apply` mutation.
- Bounded retention for orphaned factor-cache access and admission metadata.
- Hysteretic whole-cache allocated-space quotas with reserved filesystem capacity.
- Tick-ring benchmark sweeps for bounded-spin budgets and park timeouts with CPU and latency summaries.
- Opt-in native tick-ring exponential backoff, cancellation, lost-wakeup prevention, and wait metrics.
- Paced-arrival tick transport benchmarks with per-tick wake latency and CPU measurements.
- Experimental read-only zero-copy NumPy tick leases with deferred cursor release.

### Changed

- Replaced accidental wildcard root exports with an explicit public API.
- Replaced Pandas data-frame processing with Polars in the backtest path.

### Fixed

- Hardened native CSV/ZIP ownership, allocation failure, and Python reference
  cleanup paths.
- Bound the native `rho` function to the correct implementation.

### Deprecated

- None.
