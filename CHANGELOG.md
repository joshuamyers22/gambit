# Changelog

This project follows [Semantic Versioning](https://semver.org/). Changes that
have not yet been released are collected below.

## Unreleased

### Added

- An experimental owned walk-forward runner with deterministic rolling or
  expanding split identities, separate warm-up/fit/validation/held-out frames,
  two purge boundaries, a non-overlapping refit schedule, detached finite
  metrics, explicit short/ambiguous-timeline rejection, and parameter selection
  through the existing optimizer. Optimized fits use an exact column allowlist,
  fold-derived seeds, deterministic tie-breaking, selected-parameter refitting,
  and isolated held-out scoring.
- Experimental immutable point-in-time market data with separate observation,
  availability, row-revision, and dataset-revision identity; causal scalar and
  window reads; explicit missing/stale policies; bounded last-known-value age;
  strategy price and indicator-stage adapters; and automatic source fingerprint
  provenance with conflicting identity rejection.
- A versioned, human-reviewable financial acceptance corpus with manually
  calculated FIFO/multiplier/cost ledgers, lagged end-to-end execution, partial
  fills, unequal-multiplier rolls, causal VWAP, expiry cutoffs, position rejection, result
  persistence, explicit NaN/Inf and finite-overflow boundaries, and
  five NYSE holiday, observance, and year-transition calendar boundaries. Four
  fixed state-machine seeds reconcile orders, partial fills, cancellations,
  trades, two-contract FIFO ledgers, decision snapshots, group equity, and
  telemetry across execution lags 0–2.
- A deterministic financial mutation gate covering ten high-consequence risk
  and P&L changes. It runs each mutant in an isolated package, rejects invalid
  runner outcomes, and is required by the reusable CI workflow.
- A machine-readable historical-output correction ledger and owner register
  template covering risk admission, execution lag, sizing, VWAP causality and
  inputs, callback/fill integrity, and numeric failures. The accompanying policy
  defines conservative retain, rerun, and invalidate decisions.
- A persisted-research lifecycle and recovery contract covering authority,
  schema ownership, retention/deletion, result-bundle migration, verified
  backup/restore, corruption and interrupted publication, and disposable factor
  cache rebuilds. Cross-process and storage-failure acceptance exercises retain
  the last complete artifact, reject repair-in-place, and cover injected
  ``ENOSPC``/``EACCES`` failures.
- Coverage-guided Python IPC preflight fuzzing with synthetic seeds, bounded
  subprocess execution and a hash-pinned test-only engine. Native Arrow decoding
  is excluded from this target; an explicit seed-replay mode supports other hosts.

- Bounded weekly/manual CSV and ZIP fuzz campaigns with changing recorded seeds,
  compiler/run metadata and seven-day synthetic corpus/diagnostic retention.
  Per-change CI fuzz checks remain in place.

- Isolated NumPy array-data allocation-failure tests covering partial native
  results, retries, datetime conversion errors and array/allocator lifetimes;
  a dedicated unsuppressed Linux leak-check step awaits hosted qualification.
- Draft threat model and bounded CSV/ZIP coverage-guided fuzz targets, with
  sanitizer seed replay for platforms without a libFuzzer runtime.
- Native CSV input/output byte budgets and a selected-column limit, with
  checked fixed-string widths and defaults of 1 GiB input, 256 MiB output,
  and 4,096 selected columns. Read-ahead/header bytes count toward input work.
- Configurable `BundleLoadLimits` for manifest/file bytes, rows, columns, IPC
  metadata, batch counts, and decoded-payload estimates. Bundle versions 2–4 use
  the same bounded admission policy, without changing their persisted schemas.
- Frozen `OrderSnapshot` records preserve decision-time identity and built-in
  order terms independently of the live `OrderDecision.order` reference.
  New strategy results persist these fields in result-bundle version 4;
  versions 2 and 3 remain readable without fabricating missing historical terms.
- Opt-in conservative FIFO exchange-queue simulation for the native experimental
  backtester: resting best-price limits, trade-only volume-ahead depletion,
  explicit arrival audit, independent Python trace tests, and synthetic benchmarks.
- A candidate native-replay latency and capacity budget separates the proposed
  five-second FIFO objective from accepted performance, records stage/resource/
  failure boundaries, links raw characterization evidence, and enumerates the
  owner approvals and controlled measurements required before promotion.
- Experimental native top-of-book backtest prototype with a deterministic
  long-only alternating-target strategy, shared cash, displayed-size partial
  fills, fees, latency and stale-feed checks, and exact integer accounting.
  This is not a replacement for the general Strategy API; unsupported execution
  models and order types are documented explicitly.
- Direct contiguous-array tick-factor replay without live ring transport,
  with strict input-layout checks and concurrent processor-access rejection.

### Changed

- Expiring contracts now reject executions after their inclusive expiry
  timestamp and freeze P&L at the last account-grid mark at or before expiry,
  without reading post-expiry prices. The core account still does not exercise,
  assign, deliver, cash-settle, or liquidate the remaining position.
- The distribution maturity classifier is now Beta while production-readiness
  gates remain open. A canonical feature-status matrix and draft project brief
  separate release-candidate, experimental, utility, and out-of-scope behavior;
  delivery-policy tests keep package and release claims aligned.
- Whole-unit order and trade quantities now fail admission when they exceed the
  signed platform integer range required by the native FIFO kernel. Accounting
  uses overflow-checked binary64 arithmetic for weighted prices, realized and
  unrealized P&L, cumulative costs, contract aggregation, and equity; finite
  inputs that would publish infinity raise ``OverflowError``, with account trade
  batches rolled back atomically.
- HDF5 array readers now require scalar text metadata, normalize malformed JSON
  and excessive nesting to `ValueError`, and apply a combined 1 MiB manifest
  parsing budget (`max_manifest_bytes`). Trusted larger manifests require an
  explicit override; this does not bound h5py's initial attribute allocation.
- Native leak-stress checks now release their final result arrays before invoking
  LeakSanitizer and require its runtime in CI. The independent NumPy leak check
  runs after a successful native build even if the preceding stress probe fails.
- HDF5 readers now preflight every selected column before reading any payload,
  count conservative Unicode expansion in the aggregate byte budget, reject
  non-integer row/version metadata and refuse soft/external links, virtual
  datasets and external raw-data storage. Materialize linked inputs into local
  datasets; previously source-byte-only string budgets may need adjustment.
- Native CSV/ZIP ``i4``, ``i8`` and integer-backed datetime parsing now accepts
  signed extrema without undefined behavior and rejects out-of-range prefixes
  with ``RuntimeError``. Validate inputs and rerun results affected by formerly
  overflowed integers; existing in-range prefix/separator semantics are retained.
- Simulator fill membership and engine-applied fill totals now use batch-local
  identity indexes instead of repeated order/trade scans. These bookkeeping
  steps are linear in orders plus fills, preserving validation, callback order
  and rollback behavior. This is not an end-to-end backtest performance claim.
- General Python strategy scheduling and legacy debugging buckets now retain
  lists only for populated timestamps. Timestamp length, callback ordering, and
  canonical account history are unchanged. Internal bucket containers are now
  fixed-length sparse sequences rather than concrete lists; this does not bound
  signal arrays, active rule entries, or account history memory.
- Equity, bracket, and VWAP entry sizing now account for contract multipliers;
  bracket position caps use monetary notional. VWAP entry preserves all
  contracts and uses allocation sizing when no stop is configured. Invalid
  sizing inputs fail explicitly; non-positive equity suppresses new entries.
- Release publication now requires the complete CI workflow for the same commit,
  including audits, sanitizers, notebooks, and documentation. Reference CI
  installs are frozen, benchmark correctness is required, and workflow tokens
  default to read-only access without persisted checkout credentials.
- The local quality gate includes native warnings, documentation, notebook
  cleanliness, and distribution inspection; the audit covers optional runtime
  features. Native warning tooling is included in the frozen developer setup.
- The hosted package smoke test builds with its explicitly configured Python
  interpreter so wheel compatibility checks cannot drift to `.python-version`.
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

- Scope the dedicated NumPy leak probe before Python initialization so startup
  allocations do not contaminate native-call evidence. A mandatory deliberate
  NumPy buffer leak verifies detection remains active; no suppressions are added.

- Native CSV staging and NumPy conversion now use automatic ownership rather
  than dtype-dependent `void*` deletion and manual array-buffer handoffs.
  `max_rows` no longer preallocates its requested capacity or parses an extra
  row after reaching its limit. Strings retain only their requested width.
  Allocation failures become `MemoryError`; partial results are not published.
  New standalone ASan/UBSan allocation-failure probes cover CSV and ZIP reads.
  The obsolete native demo functions with developer-local paths were replaced
  by an argument-driven smoke executable as part of the internal API migration.
- Result loading now validates every member before materializing any table and
  decodes checked byte snapshots instead of reopening paths. Malformed metadata,
  duplicate JSON keys, special/symlink members, forged Arrow dimensions and
  buffer references, and budget violations fail with `BacktestBundleError`.
  Compressed, nested, dictionary, and extension/custom-metadata layouts are now
  explicitly unsupported; normalize custom analytics in a trusted environment.
  These checks bound payload work, not total process RSS or native-decoder risk.
- `MaxPositionQuantity` now checks independently reachable long/short positions
  instead of netting opposite pending orders. Cancellation requests reserve
  remaining exposure until acknowledged, and reducing an existing breach cannot
  create an opposite-side breach. Rerun backtests whose admissions relied on
  pending-order netting or breach-reducing overshoots; prior decision identities
  affected by order mutation cannot be reliably reconstructed from old bundles.
- Standalone ``decide_order`` now validates proposed and open pending quantities
  before any policy runs, even when no policies are configured. Mutated fractional
  or NaN quantities can no longer receive acceptance; invalid inputs raise using
  the constructor whole-unit rule. Valid signed quantities, terminal context
  orders with zero remainder and unscheduled pre-trade proposals remain supported.
- VWAP stops now require finite real values or floating NaN (explicitly no stop)
  at construction, rule admission and built-in simulator preflight. Infinite
  stops can no longer silently disable the trigger, and booleans/non-real values
  fail explicitly. Use NaN to disable a stop and rerun affected backtests;
  valid stop direction, prorating and cancellation behavior are unchanged.
- VWAP execution windows are revalidated at rule admission and before built-in
  simulation, using the constructor policy. Mutated NaT, malformed or reversed
  end times can no longer trigger immediate backup-price fills. Invalid windows
  reject the execution batch before fills; valid zero-duration windows remain
  supported. Rerun backtests affected by invalid mutated window terms.
- The built-in simple simulator now rechecks mutable limit prices at the
  marketability comparison, after price and slippage callbacks. NaN/Inf limits
  can no longer silently execute as market orders; invalid values fail before
  fills are applied. Finite negative/zero limits remain supported.
- Strategy-expanded roll pairs now receive deterministic, strategy-local
  submission IDs instead of Python memory addresses. Replays preserve roll
  metadata, and separate submissions retain separate pairs even when reusing
  a source roll command. Standalone leg expansion remains process-local.
- VWAP fills forced before their requested end time at a calendar-day boundary
  now use only observations at or before execution. Future prices and volumes
  previously leaked into the fill price and P&L. Affected historical VWAP
  backtests must be rerun; existing trigger and quantity policies are unchanged.
- Rule admission now rechecks mutable contract, timestamp, time-in-force and
  status types using the constructor policy. String/integer expiry policies
  can no longer silently bypass DAY/FOK handling; malformed fields reject the
  batch before risk evaluation. Unscheduled construction remains supported,
  but submission requires a valid timestamp matching the strategy heartbeat.
- Rule-return batches now reject repeated order objects and resubmission of
  captured pending orders before risk evaluation. This prevents conflicting
  decisions on the same mutable order and duplicate history entries. Distinct
  orders with identical values remain valid, including separate roll commands.
- Rule submission now rechecks mutable order quantities and limit prices using
  constructor numeric validation, before risk evaluation. NaN/Inf, fractional
  or zero quantities, booleans and numeric strings cannot bypass admission;
  invalid batches are rejected in full. Finite negative/zero limits remain valid.
- Roll expansion now rechecks mutable contract and quantity terms using the
  construction-time policy. Same-direction legs, identical/cross-group contracts
  and invalid quantities fail before submission. Non-open roll commands cannot
  be expanded into fresh open legs. Valid unequal-sized rolls remain supported.
- Rule, simulator and risk-policy callbacks now preserve protected order identity
  (contract reference, submission timestamp and time-in-force). Rules retain
  cancellation support but cannot resize or fill pending orders; risk policies
  cannot mutate proposed/pending quantities or statuses. Failure or interruption
  restores the protected identity and lifecycle fields without undoing earlier
  valid simulator fills. Custom metadata and other unprotected state are outside
  this scoped rollback guarantee.
- Simulator-return and direct-account boundaries now revalidate mutable trade
  references and timestamps using the construction-time policy. Fills before
  their originating order, missing submission timestamps, and invalid reference
  types fail before accounting changes. Valid earlier off-grid order timestamps
  remain supported for historical account imports.
- Mutable trade quantities, prices, fees and commissions are revalidated before
  simulator fills or direct account imports can affect accounting. Construction
  and ingestion share the same numeric policy; finite negative prices and fee
  rebates remain valid. Invalid direct-import batches leave existing history and
  valuation intact rather than silently truncating quantities or propagating NaN.
- Market-simulator callbacks now reject opposite-direction fills and aggregate
  overfills against the remaining quantity captured before callback execution,
  even when the simulator directly changes order state. Mutable fill quantities
  are revalidated as finite, nonzero whole units before account mutation.
- General Strategy execution now withholds orders from every market simulator
  until their configured heartbeat lag has elapsed. Cancellation and DAY expiry
  are processed while orders wait; FOK orders retain their existing eligibility
  window. Earlier results with lag greater than one may contain premature fills
  and should be rerun. Native experimental execution models are unchanged.
- Run configuration now rejects fractional/nonfinite integer settings, non-boolean
  flags, invalid equity types and duplicate YAML keys. Strategy runs capture actual
  runtime options and ordered execution-component descriptions, with unresolved
  callback dependencies explicit; detected mid-run drift prevents result publication.
  Result bundles now write format 3 and retain explicit format-2 reading support.
- Regenerate Cython P&L code for each isolated build so cached declarations from
  another Python/NumPy environment cannot break native compilation.
- Trading-day offsets now reject date and timestamp overflow instead of wrapping
  into incorrect dates, and preserve valid timestamps at the nanosecond limits.
- Trading-day offsets now require integer counts for every roll mode, preventing
  scalar fractions from being truncated and strings or booleans from becoming offsets.
- Calendar APIs now reject Polars columns without a Date or Datetime dtype,
  and trading-day membership rejects non-date NumPy arrays, preventing numeric
  values from being silently interpreted as dates since the Unix epoch.
- Calendar conversion now preserves the adapter's trading weekdays and accepts
  empty holiday lists, fixing continuous calendars and nonstandard trading weeks.
- Trading-day counts now support zero-dimensional NumPy date arrays, including
  missing dates, while preserving the broadcast result shape and caller inputs.
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
- Every plotted x-value must now have a matching detail row, preventing opaque
  hover-count indexing failures and empty click-through results.
- Interactive pivots now apply documented initial filter selections atomically
  and render their initial state exactly once.
- Cascading interactive filters now incorporate each newly selected upstream
  value and suppress recursive partial-state renders.
- Interactive confidence summaries now reject nonnumeric, infinite, or reversed
  bounds while retaining explicitly missing intervals.
- Coverage policy now protects interactive reporting at 80% and raises the
  optimizer and numerical utility floors to 70% and 50%.
- Volatility and VaR target sizing now reject nonzero forecasts whose fitted
  risk model reports zero risk instead of silently flattening the portfolio.

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
