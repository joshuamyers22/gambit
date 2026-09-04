# Changelog

This project follows [Semantic Versioning](https://semver.org/). Changes that
have not yet been released are collected below.

## Unreleased

### Changed

- ``RollOrder`` is now a validated atomic market-roll command. It expands into
  outgoing and incoming legs in the same contract group, and the built-in
  simulator fills both legs or neither when price data is unavailable.
- ``StopLimitOrder`` is deprecated and rejected at execution boundaries. Use
  an explicit trigger rule that emits a ``MarketOrder`` or ``LimitOrder``.
- Futures and E-mini option symbols now use deterministic year decoding:
  one-digit years denote 2020–2029 standard symbols, while historical years
  use an explicit two-digit suffix (for example, ``ESZ6`` is December 2026 and
  ``ESZ16`` is December 2016).

### Fixed

- Single-process optimization now evaluates every yielded suggestion and
  returns each result to adaptive generators without skipping the next value.
- Optimizer configuration now rejects a zero pending-task limit, and experiment
  dataframes support auxiliary costs that are absent from some results.
- Utility zero-shifts now return an unchanged copy, and recursive file lookup
  now searches the requested directory with deterministic ordering.
- Array shifting now chooses fill values representable by integer, temporal,
  and string dtypes; closest-value lookup handles singleton arrays and rejects
  empty inputs explicitly.
- Weekly option expiry construction now rejects nonexistent fifth weekdays
  instead of silently drifting into the following contract month.
- Rolling windows, rounding increments, bucket boundaries, and frequency
  inference now reject invalid or underdetermined inputs explicitly.
- Optimizer plots now handle all-invalid or fully filtered experiment sets and
  preserve sparse auxiliary metrics across every experiment.
- Trade-bar resampling now executes supplied custom aggregations and validates
  time columns, custom column names, and paired series lengths.
- CI now rejects duplicate YAML mapping keys; the shadowing legacy dependency
  audit job was removed so the pinned lock-based audit actually runs.
- Local and CI checks now enforce module-specific coverage floors for supported
  market, calendar, optimizer, and numerical utility policy.
- Return evaluation now converts all non-finite values to actual zeros when
  requested, and three-year windows are leap-day safe and cutoff-inclusive.
- Return evaluation now annualizes regular calendar-month observations at 12
  periods per year, including correct multiples for multi-month sampling.
- All-non-finite return histories now fail as no data unless callers explicitly
  opt into replacing leading non-finite observations with zeros.
- Drawdown extrema now ignore missing observations, return defined missing
  results for all-missing arrays, and plotting no longer writes debug values.
- Cross-platform CI typing now tolerates current NumPy stub precision, and the
  multiple-contract notebook canonicalizes overlapping price timestamps.
- Percentile ranking now handles singleton and tied values deterministically
  and rejects multidimensional or non-finite inputs.
- CSV exports now use collision-resistant same-directory staging and clean up
  partial files when serialization fails.
- Interactive confidence bands now preserve the bootstrap lower and upper bound
  ordering instead of labeling them in reverse.
- Bootstrap intervals now validate their sampling configuration, support seeded
  generators, and use the same statistic as the interactive center estimate.
- Percentile plot buckets now preserve missing observations and validate an
  exact positive bucket count.
- Visualization, notebook, documentation, and aggregate extras now install
  Plotly's required `anywidget` runtime for `FigureWidget`.
- Interactive plots now cycle the default palette deterministically when more
  than ten series are rendered.
- Confidence-band fills now accept Plotly's native color formats, including
  hexadecimal, named, and HSL colors.
- Interactive line thickness configuration is now applied and invalid widths
  fail at configuration time.
- Interactive plot line data now rejects duplicate series, malformed summary
  widths, inconsistent x columns, and unusable detail frames before rendering.

## 1.1.0 - 2026-08-31

### Added

- Fail-closed release artifact inspection and isolated installation gates for
  core, optional extras, native extensions, the CLI, and source distributions.
- Deterministic CSV, ZIP, and HDF5 boundary fuzz smoke coverage in the native
  sanitizer workflow, with checked-in seeds for reproducible failures.
- Independent randomized reference comparisons for Cython FIFO P&L, native
  mapped columns, and multi-instrument tick-factor aggregation.
- Linux and macOS CI gates that compile every project-owned C++ translation
  unit with strict warnings-as-errors while explicitly excluding generated and
  vendored sources.
- Explicit unit, integration, native, fuzz, notebook, and non-blocking
  performance test suites with documented local selectors and dedicated CI lanes.
- Architecture, heartbeat event-flow, and accounting/execution assumption
  documentation with explicit reconciliation requirements and model non-goals.
- Documentation and notebook drift gates covering notebook validity and output
  cleanliness, deterministic post-execution normalization, and Git-clean sources.
- Schema-versioned HDF5 dataframe manifests, recoverable pending/backup group
  publication, bounded reads and writes, and corruption regression coverage.
- Preflight-validated bulk construction of ordered contract universes and
  sector groups, with shared metadata defaults, per-contract overrides, and
  direct ``StrategyBuilder`` integration for thousands of instruments.
- Point-in-time, shrinkable covariance estimates with immutable matrices,
  volatility and adverse-correlation stress transformations, additive component
  risk, diversification ratios, and conservative portfolio risk overlays.
- Executable covariance-risk and overlay documentation with automated regression
  coverage.
- Immutable point-in-time FX snapshots, explicit base-currency exposure
  translation, and retained local-value audit columns.
- Executable multi-currency risk examples and regression coverage.
- Covariance-aware volatility-targeted exposure sizing with an explicit,
  auditable portfolio-overlay stage.
- Hierarchical exposure clipping, atomically persisted trading overrides, and
  rolling executed-and-pending quantity budgets.
- Point-in-time historical and Gaussian value at risk and expected shortfall,
  plus auditable VaR-targeted exposure sizing.

### Changed

- Manual TestPyPI publishing is now an explicit workflow opt-in after validation.
- macOS release wheels use supported macOS 15 Intel and ARM runners and declare
  deployment targets matching their bundled Homebrew native libraries.
- Core imports no longer require visualization, Statsmodels/SciPy, exchange
  calendar, HDF5, or notebook dependencies; these are provided by granular
  runtime extras and loaded only when their features are used.
- Accounts and return evaluation now reject non-finite or non-positive starting
  equity, and empty return or market-data inputs have explicit behavior.
- Strategy timestamps, rule results, simulated trades, callback prices, and
  numeric market-data columns now fail early at their public boundaries with
  contextual errors.
- Risk calculations reject measures whose market data is newer than the explicit
  calculation cutoff unless look-ahead is enabled.
- Risk results carry explicit units, and aggregation preserves measure, scenario,
  and unit boundaries to prevent semantically invalid totals.

### Fixed

- Clearing the global contract cache now also clears contract references held
  by cached groups.
- Missing account marks now carry the previous unrealized P&L instead of
  contaminating subsequent account equity with ``NaN``.

## 1.0.0 - 2026-08-29

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
- Atomic, cross-process factor-cache lifetime metrics with bounded Prometheus export.
- Non-mutating cache health thresholds, Linux device-write telemetry, and operational scheduling guidance.
- Backward-compatible native segment v3 with XXH64 chunk validation.
- Bounded, dry-run-first, lease-safe, resumable v1/v2-to-v3 factor migration and benchmarks.
- A deterministic synthetic 4/16 moving-average crossover regression.

### Changed

- Replaced accidental wildcard root exports with an explicit public API.
- Replaced Pandas data-frame processing with Polars in the backtest path.
- Publish under the `gambit-markets` distribution while preserving the `gambit` import namespace.

### Fixed

- Hardened native CSV/ZIP ownership, allocation failure, and Python reference
  cleanup paths.
- Bound the native `rho` function to the correct implementation.
- Clear stale contracts from cached contract groups without replacing the process-wide default singleton.

### Deprecated

- None.
