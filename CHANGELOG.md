# Changelog

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

### Changed

- Replaced accidental wildcard root exports with an explicit public API.
- Replaced Pandas data-frame processing with Polars in the backtest path.

### Fixed

- Hardened native CSV/ZIP ownership, allocation failure, and Python reference
  cleanup paths.
- Bound the native `rho` function to the correct implementation.

### Deprecated

- None.
