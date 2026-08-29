# Gambit Adversarial Review and Improvement Plan

## Review status

- Review date: 2026-08-26
- Reviewed tree: `Downloads/pyqstrat-master`
- Package version: `1.0.2`
- Scope: Python, Cython, C++, packaging, tests, examples, generated documentation, and operational behavior
- Method: static inspection plus a local `pytest --collect-only` attempt
- Constraint: the available interpreter is Python 3.9.6 while the project requires Python 3.10+, and project dependencies/native extensions are not installed. Runtime findings below are therefore separated from findings confirmed directly from source.

### Modernization update (2026-08-27)

The project has since been renamed to Gambit, moved to a `src/` layout, built in
Python 3.12 with NumPy 2.5 and Polars 1.44, and given a focused regression suite.
The folder is now `Downloads/gambit`; the import and distribution name are both
`gambit`. Public tabular APIs now return `polars.DataFrame` objects with explicit
timestamp columns, and pandas is no longer a direct Gambit dependency or source
import. `pandas-market-calendars` and `statsmodels` may still install pandas as a
transitive implementation dependency. The four confirmed silent-result defects
are fixed. Legacy full-module typing and optional doctest cleanup remain explicit
follow-up debt rather than being hidden in the default test suite.

All example notebooks now use Polars. Five end-to-end examples plus the offline
data-creation notebook are CI smoke tests; the optimizer smoke test also exercises
the retained statsmodels analytics path. The data-creation function itself needs
an external HDF5 archive and is intentionally not invoked by CI.

## Executive assessment

pyqstrat is a quantitative-strategy backtesting library centered on a callback-driven `Strategy`, an `Account`/P&L ledger, reusable trading rules and market simulators, return evaluation, portfolio aggregation, parameter optimization, plotting, calendars, HDF5/CSV I/O, and native acceleration.

The code is compact and exposes useful primitives, but it is not ready to be trusted for financial decisions without a correctness hardening pass. The highest risks are silent data-selection and accounting errors: several public methods can return plausible but incorrect results rather than fail loudly. Packaging and test discovery are also fragile enough that regressions may escape detection. Native I/O increases the need for fuzzing and sanitizer coverage.

Recommended posture: treat all generated P&L, filtered reports, order histories, and optimizer rankings as unverified until the P0 and P1 work below is complete.

## Project outline

| Area | Primary files | Responsibility |
| --- | --- | --- |
| Strategy orchestration | `strategy.py`, `strategy_builder.py` | Registers indicators/signals/rules, runs market simulation, manages orders, exposes reports |
| Domain model | `pq_types.py`, `markets.py` | Contracts, groups, prices, orders, trades, futures/options |
| Accounting | `account.py`, `compute_pnl.pyx` | Positions, realized/unrealized P&L, equity, round trips |
| Reusable components | `strategy_components.py` | Price lookup, market simulators, sizing, entry/exit rules |
| Analytics | `evaluator.py`, `portfolio.py` | Returns, drawdowns, metrics, portfolio aggregation |
| Optimization | `optimize.py` | Single- and multiprocess parameter experiments and plots |
| Data/time utilities | `pq_io.py`, `pq_utils.py`, `holiday_calendars.py` | HDF5, native CSV, resampling, dates, calendars, configuration |
| Visualization | `interactive_plot.py` | Plotly/Jupyter interactive reports |
| Native extensions | `compute_pnl.pyx`, `src/gambit/cpp/**` | P&L acceleration, option math, CSV/ZIP parsing |
| Factor cache/performance research | proposed `factor_cache.py`, native ring-buffer module | Content-addressed factor DAGs, NVMe-backed mapped columns, bounded producer/consumer transport |
| Examples/docs | `examples/notebooks/**`, `documentation/**` | Executable tutorials and API documentation |
| Build/release | `setup.py`, `requirements.txt`, `MANIFEST.in`, shell scripts | Compilation, dependency declaration, packaging |

## Adversarial findings

### P0 — fix before relying on results

#### 1. Trades can be indexed under the wrong contract

`Account.add_trades` uses `contract.symbol` while populating `_trades_for_date` after the earlier loop that assigned `contract` has finished (`account.py`, around lines 387-401). Every trade in that final loop can therefore be stored under the last contract encountered, corrupting contract-specific trade lookup and downstream P&L calculations.

Proposed change:

- Replace the stale variable with `trade.contract.symbol`.
- Add a regression test with interleaved trades for at least two symbols on the same and different dates.
- Assert that contract/date buckets, positions, realized P&L, round trips, and account totals reconcile independently.

#### 2. Contract-group order filtering compares incompatible types

`Strategy.orders` iterates `contract_group.contracts`, which is a dictionary keyed by symbol, then compares `order.contract == contract` (`strategy.py`, around lines 608-621). This compares a `Contract` to a string key and can silently return no matching orders.

Proposed change:

- Filter by group identity/name or iterate `contract_group.contracts.values()`.
- Define whether contract equality is identity-, symbol-, or value-based and apply that definition consistently.
- Test empty groups, multiple contracts, roll contracts, `None` contracts, and date boundaries.

#### 3. Optimizer ranking semantics are reversed

`Optimizer.experiment_list` sorts `lowest_cost` with `reverse=True` and `highest_cost` with `reverse=False` (`optimize.py`, around lines 128-145). This can select the worst experiment while presenting it as the best.

Proposed change:

- Reverse the two sort directions.
- Add exact ordering tests including ties, negative values, infinities, and NaNs.
- Decide whether invalid experiments should be excluded consistently from both `experiment_list` and `df_experiments`.

### P1 — correctness and reliability

#### 4. `Strategy.df_data` ignores its date filter

The method creates and filters a local `timestamps` array, but constructs each result from `self.timestamps` and never slices indicator/signal arrays (`strategy.py`, around lines 524-570). `start_date` and `end_date` therefore do not constrain output.

Proposed change:

- Build one boolean/index mask and apply it to timestamps, all indicator arrays, all signal arrays, and P&L.
- Validate array lengths and fail with a useful error on misalignment.
- Test open/closed endpoints, no-match ranges, single-row ranges, and timezone/date-unit variants.

#### 5. Multiprocessing is non-portable and potentially unsafe

`Optimizer` restricts Windows to one process but `_run_multi_process` always requests `mp.get_context('fork')` (`optimize.py`, around lines 78-124). `fork` is unavailable on Windows and is increasingly problematic with threaded/native runtimes; the special case also leaves portability gaps on macOS.

Proposed change:

- Make the multiprocessing context configurable, defaulting to a platform-safe method such as `spawn` or the interpreter default.
- Bound the number of in-flight futures instead of eagerly submitting the complete generator.
- Preserve generator feedback semantics or explicitly document that adaptive generators require single-process mode.
- Add spawn-based Linux/macOS/Windows tests, worker exception tests, cancellation, and keyboard-interrupt cleanup.

#### 6. Test discovery imports nearly every Python file

`pytest.ini` sets `python_files = *.py` and enables module doctests. Collection consequently attempts to import application modules, notebook helpers, Sphinx configuration, and `setup.py`. The local collection attempt produced 37 errors before collecting tests; missing dependencies and Python 3.9 account for many errors, but the overly broad discovery pattern itself is confirmed.

Proposed change:

- Move tests to `tests/` and restore a narrow `test_*.py` pattern.
- Run doctests as a separate explicit job against selected modules.
- Keep docs/import smoke tests separate from unit tests.
- Add markers for native, plotting, notebook, slow, and platform-specific tests.

#### 7. Package import is all-or-nothing and has a very broad API

`pyqstrat/__init__.py` wildcard-imports every Python subsystem and both native extensions. Importing any lightweight utility therefore requires plotting/Jupyter/HDF5/calendar dependencies and successful native builds. Wildcard exports also make API compatibility and name collisions difficult to control.

Proposed change:

- Define an explicit `__all__` and documented public surface.
- Split optional extras (`plot`, `io`, `notebooks`, `dev`) and lazily import optional features.
- Produce targeted errors when a native extension is unavailable.
- Add minimal-import and optional-dependency tests.

#### 8. Build configuration is environment-specific and falsely claims portability

`setup.py` refuses to run unless `CONDA_PREFIX` or `CONDA_PREFIX_1` is set, uses `distutils`, assumes `libzip`, applies `-Ofast`, declares `platforms='any'`, and executes build-time imports before isolated build requirements can be established. `requirements.txt` has very old lower bounds and no upper constraints despite Python 3.10+.

Proposed change:

- Adopt `pyproject.toml` with declared build requirements and a modern backend.
- Discover libraries through standard compiler/pkg-config mechanisms rather than requiring Conda.
- Publish platform-tagged wheels; do not claim `any` for native packages.
- Replace `-Ofast` with safer optimization unless fast-math behavior is explicitly accepted and tested.
- Establish and CI-test a supported dependency matrix; separate runtime and optional dependencies.

### P2 — hardening and maintainability

#### 9. Native CSV/ZIP I/O needs hostile-input testing

The C++ parser performs manual buffer management, delimiter mutation, dtype dispatch, raw allocation, and Python/NumPy ownership transfer. The 2026-08-27 ownership review confirmed leaks when NumPy conversion failed partway through, a leaked dtype descriptor for string columns, a leaked Python reference on a datetime failure path, and process-lifetime ZIP descriptor retention. The global ZIP cache also allowed an archive to be closed while a concurrent member reader still referenced it. These paths have been removed or repaired, and descriptor-lifetime and repeated-failure regressions were added.

The same review added mapped-segment arithmetic checks, failure cleanup for unpublished mapped files, pre-allocation ring-capacity validation, finite timeout validation, and a native allocation stress probe. All project-owned native extensions rebuild with compiler warnings enabled; the only remaining local build warning is in generated Cython output. Linux CI now passes the native ASan/UBSan boundary suite and the LeakSan allocation/lifetime stress probe. CPython and NumPy process-global allocator caches are narrowly suppressed by allocator frame; raw project-owned C++ allocations remain visible. macOS still prevents runtime interceptor injection for this Python executable, and the platform `leaks` tool cannot acquire a task port even with the approved unsandboxed run. Therefore the source review and sanitizer stress tests are strong evidence of improved ownership safety, not a proof that no native leak can exist.

Residual risks:

- The CSV bridge still uses type-erased raw vector pointers. Its known cleanup paths are covered, but replacing this with RAII column objects remains preferable before treating untrusted native ingestion as production-hardened.
- `TickRing` is strictly single-producer/single-consumer. Concurrent producer or consumer misuse is outside its memory model and must remain experimental until runtime ownership enforcement or a different queue is implemented.
- The bundled Lets Be Rational sources are third-party numerical code and were checked for integration ownership, not re-audited line by line as project-owned code.
- LeakSanitizer is required and passing in the Linux native stress job. The SPSC algorithm has been extracted into a reusable project-owned core; its standalone two-thread stress test passes under Linux ThreadSanitizer after transferring one million ordered records. Local macOS tooling did not provide a usable dynamic leak/race report.

Proposed addition:

- Add libFuzzer/Atheris-style fuzz targets and a corpus of malformed CSV/ZIP files.
- Run AddressSanitizer and UndefinedBehaviorSanitizer in CI.
- Enforce row, field, allocation, and decompressed-size limits.
- Verify exception-safe cleanup and NumPy buffer ownership on every failure path.
- Document that input files are untrusted and specify supported CSV semantics.

#### 10. Financial invariants are under-tested

Most tests live inside production modules or notebooks, with only a small number of named test functions for roughly 6,800 lines of Python/Cython plus C++. Core edge cases—partial fills, flips through zero, fees, multipliers, missing prices, duplicate/out-of-order timestamps, rolls, stop/limit behavior, expiry, and NaN/Inf—lack an evident systematic suite.

Proposed addition:

- Add property-based tests for conservation and reconciliation invariants.
- Compare Cython P&L against a simple independent Python oracle.
- Require `ending equity = starting equity + cumulative net P&L` within a defined tolerance.
- Reconcile trades to positions and realized/unrealized P&L by symbol and contract group.
- Add randomized event sequences and deterministic seeds.
- Create golden scenarios for long, short, scale-in/out, cross-zero, commission/fee, multiplier, and expiry behavior.

#### 11. Broad exception reconstruction may obscure failures

Strategy and optimizer paths catch `Exception` and instantiate `type(e)` with a new message. Some exception classes do not accept a single string, and worker-side tracebacks are not the parent traceback. This can replace the original failure with a secondary error or misleading trace.

Proposed change:

- Raise a project-specific exception with contextual fields and `raise ... from e`.
- Preserve rule/simulator name, symbol/group, timestamp/index, and optimizer suggestion.
- Test custom exception constructors and remote worker tracebacks.

#### 12. HDF5 writes need schema and crash-safety guarantees

The HDF5 writer derives row metadata from the last array, does not visibly reject unequal column lengths, replaces the destination key after building a temporary group in the same file, and `hdf5_repack` uses a predictable `.tmp` sibling followed by `os.rename`.

Proposed change:

- Validate column names, equal lengths, dtype support, and reserved temporary-key collisions before mutation.
- Store an explicit schema/version and preserve datetime units and Unicode behavior.
- Use unique temporary files/groups and document atomicity limits.
- Add interrupted-write, corrupt-file, nested-key, concurrent-access, empty-frame, Unicode, and unequal-length tests.

#### 13. Global state and hidden side effects reduce composability

Utilities and plotting code modify pandas and Plotly global options; some restoration is conditional and not protected by `finally`. Package import therefore may change process-wide behavior.

Proposed change:

- Move global configuration behind explicit APIs or context managers.
- Restore options with `try/finally` or pandas option contexts.
- Add tests proving imports and plotting failures leave global state unchanged.

#### 14. API contracts and typing are inconsistent

Examples include methods annotated to return a `DataFrame` that can return `None`, date defaults passed through `np.datetime64(None)`, mutable/domain registries with implicit global behavior, and placeholder/debug symbols such as `foo` in `interactive_plot.py`.

Proposed change:

- Run strict-enough `mypy`/`pyright` and Ruff, then fix contracts rather than suppressing mismatches.
- Define validation rules for timestamps, quantities, prices, sortedness, uniqueness, and finite values at public boundaries.
- Remove dead/debug APIs and publish a deprecation policy before changing public names.

#### 15. Documentation and release artifacts can drift

Generated HTML is committed, notebooks appear to be a source for generated Python sections, and `gen_py_files.sh` contains only a local invocation. There is no evident CI configuration in the reviewed archive, and no obvious contribution/security/change-log guidance.

Proposed addition:

- Declare the source of truth for notebook-generated modules and verify regeneration produces no diff.
- Build docs in CI rather than reviewing generated HTML as source changes.
- Add `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, release instructions, and architecture/invariant documentation.
- Add notebook execution smoke tests with pinned sample data expectations.

#### 16. NVMe-mapped factor caching and ring-buffer synchronization require proof, not intuition

Research backtests repeatedly materialize related factor columns. A factor tree
could reuse the output of each parent node by storing immutable column segments
directly in an NVMe-backed `mmap` region and publishing segment descriptors
through a bounded ring buffer. This may reduce RAM pressure and redundant factor
calculation, but a naive spinlock design can make performance worse or corrupt
research results.

Adversarial risks:

- A Python spinlock is constrained by the GIL and is not a valid substitute for
  native atomic operations.
- Busy-waiting while a producer or consumer is blocked on a page fault, writeback,
  or NVMe latency can consume an entire core indefinitely.
- No spinlock or ring-slot claim may cover `mmap`, page-fault, flush, allocation,
  checksum, compression, or filesystem operations.
- Mapped writes are not transaction boundaries. Process termination can leave a
  valid-looking header pointing at partially written column bytes.
- False sharing between producer/consumer cursors can erase expected gains;
  cursors and hot counters need cache-line separation.
- Multiple processes require a documented memory model and process-shared atomic
  primitives; thread-only atomics are insufficient.
- Cache keys that omit source fingerprints, transform code/version, parameters,
  schema, row ordering, calendar, or floating-point mode can return plausible but
  incorrect factors.
- Mutable mapped columns can allow one factor stage to corrupt an ancestor used by
  other branches. Published segments must be immutable.
- NVMe capacity, endurance, thermal throttling, filesystem behavior, and page-cache
  eviction can dominate benchmark results.
- Unbounded factor trees can exhaust address space, file descriptors, mapped pages,
  or disk even when physical RAM usage appears stable.

Proposed design for evaluation:

- Represent a factor tree as a deterministic DAG. Each node key hashes parent
  keys, input-data fingerprints, transform identity/version, normalized parameters,
  output schema, row count/order, and calculation/provenance context.
- Store each output column in an immutable, page-aligned segment of a preallocated
  NVMe-backed mapping. Keep validity/null data and variable-width offsets/data in
  separately described mapped segments using an Arrow-compatible physical layout
  where practical.
- Put only fixed-size descriptors in the ring buffer: cache key, mapping/file id,
  segment offset, byte length, dtype/schema id, row count, checksum, and publication
  generation. Do not copy whole columns through ring slots.
- Use a two-phase publication protocol. A writer reserves space and writes column
  bytes/checksum first, then atomically publishes a committed descriptor. Readers
  ignore uncommitted generations and verify length/schema/checksum before exposing
  the column.
- Keep the allocation journal/manifest separate from data segments. Update it using
  generation numbers and crash-recovery scanning; never assume `mmap.flush()` alone
  provides application-level atomicity or durability.
- Start with a single-producer/single-consumer native ring using head/tail atomics.
  Only consider multi-producer or multi-consumer operation after the simpler memory
  model is verified under ThreadSanitizer and process-crash tests.
- If contention requires waiting, use a bounded adaptive strategy: short native
  spin, exponential backoff, then park on an OS primitive. Never spin across NVMe
  work or wait indefinitely.
- Expose mapped segments to NumPy/Arrow/Polars with zero-copy views only while a
  lease pins the segment against eviction. Make lifetime and read-only guarantees
  explicit.
- Use reference counts or leases plus an eviction policy constrained by mapped
  bytes, resident-set pressure, and free NVMe capacity. Reclaim only unpublished or
  unleased generations.
- Provide a safe pure-Python/reference implementation using ordinary immutable
  files and a bounded queue so native results and failure behavior have an oracle.

Implementation gate:

- Do not implement the native spin/ring path until benchmarks compare it with
  Polars lazy execution, Arrow IPC/Parquet scans, ordinary `mmap`, and a blocking
  bounded queue.
- Measure cold/warm latency, rows and bytes per second, CPU time, page faults,
  context switches, peak/resident memory, NVMe bytes written, cache hit ratio, and
  end-to-end factor-tree completion time.
- Test narrow/wide columns, fixed/variable-width data, null-heavy data, branch reuse,
  cache misses, oversubscription, forced eviction, process crashes, truncated
  mappings, checksum failure, concurrent readers, and producer/consumer imbalance.
- Require a material end-to-end improvement on representative research workloads,
  not only a synthetic ring-buffer throughput win.
- Correctness, deterministic fingerprints, crash recovery, and bounded resource use
  remain release blockers even if throughput improves.

## Implementation roadmap

### Phase 0 — establish a reproducible baseline

- [x] Add `pyproject.toml` and document the Python/dependency baseline; a lock/constraints policy remains open.
- [x] Create a clean Python 3.12 environment and build all three native extensions.
- [x] Narrow test discovery and establish the executable baseline.
- [x] Add Linux/macOS CI across Python 3.10/3.12 plus source distribution and wheel checks; Windows native CI remains open.
- [x] Add formatting, linting, and incremental typing checks; dependency/security scanning remains open.

Exit criteria: a fresh checkout can be built and tested using documented commands without requiring an implicit developer machine configuration.

### Phase 1 — stop silent financial/reporting corruption

- [x] Fix stale-symbol indexing in `Account.add_trades` and add multi-symbol reconciliation tests.
- [x] Fix contract-group order filtering and add date/group boundary tests.
- [x] Fix optimizer sort direction and add selection/ranking tests.
- [x] Fix `Strategy.df_data` date masking and array-alignment validation.
- [x] Add an independent FIFO ledger oracle and compare randomized trade streams against the native engine.
- [x] Add a golden account scenario reconciling position, realized/unrealized P&L, costs, net P&L, and equity.
- [x] Make round-trip reporting repeatable without mutating source trades or their property namespaces.
- [x] Add golden lifecycle scenarios for partial fills, invalid orders, cancellation, cross-zero reversal, multipliers, and expiry.
- [x] Add an end-to-end short strategy covering rule dispatch, market simulation, scale-in/out, FIFO round trips, and equity reconciliation.

Exit criteria: each defect has a failing-before/passing-after regression test, and all golden scenarios reconcile at symbol, group, and account levels.

### Phase 2 — harden execution and public boundaries

- [ ] Validate timestamp ordering/uniqueness, finite prices/quantities, contract membership, and callback return types.
- [ ] Replace reconstructed exceptions with contextual chained exceptions.
- [x] Make multiprocessing spawn-portable and bound in-flight work.
- [x] Cancel queued work and wait for worker cleanup on failure or interruption.
- [ ] Make optional dependencies and imports granular.
- [ ] Define tolerances and behavior for NaN, Inf, missing marks, zero equity, and empty datasets.

Exit criteria: invalid inputs fail early with stable, documented exceptions; multiprocessing tests pass under spawn; minimal imports work without visualization dependencies.

### Phase 3 — secure native and persistence boundaries

- [ ] Fuzz native CSV/ZIP parsing and HDF5 schema operations.
- [x] Run ASan/UBSan native boundary tests in Linux CI and add a LeakSanitizer allocation/lifetime stress probe.
- [ ] Make compiler warnings errors for all project-owned C/C++; the extracted SPSC core and standalone ThreadSanitizer target already use `-Wall -Wextra -Wpedantic -Werror`.
- [x] Audit native ownership/failure paths and add repeated-allocation, descriptor-lifetime, malformed-input, mmap-lifetime, and ring stress probes.
- [ ] Add resource limits and malformed-input tests.
- [ ] Make HDF5 writes schema-versioned and as crash-safe as documented.
- [ ] Verify Cython/native results against independent reference implementations.

Exit criteria: fuzz smoke runs and sanitizers are clean, corpus regressions are checked in, and ownership/resource limits are documented.

### Phase 4 — API, documentation, and release quality

- [x] Return an immutable `BacktestResult` snapshot from `Strategy.run`, including detached Polars trade, order, decision, and P&L frames plus provenance.
- [x] Capture per-phase wall/CPU timings and order/trade lifecycle counters without coupling the production path to the experimental native cache.
- [x] Add deterministic result-bundle serialization using atomic directory publication, a canonical manifest, and schema-versioned Polars IPC files.
- [x] Attach explicitly requested risk/stress results and market-data validation findings to result bundles, with analytics timed separately from execution.
- [x] Establish explicit root exports, semantic versioning, deprecation, and changelog policy.
- [ ] Separate unit, integration, native, notebook, plotting, and performance suites.
- [ ] Add architecture diagrams and document accounting/execution assumptions.
- [ ] Automate docs/notebook regeneration checks.
- [ ] Build and install wheels in clean environments before release.

Exit criteria: public API and financial assumptions are documented, generated artifacts cannot silently drift, and release artifacts install and import on every supported platform.

### Phase 5 — factor DAG and NVMe-mapped column cache research

- [x] Add representative branching factor-tree workloads and a reproducible no-cache/cache benchmark harness; committed machine baselines remain pending.
- [x] Specify canonical factor-node hashing, schemas, lineage, and invalidation rules.
  `FactorNodeIdentity` hashes a versioned canonical JSON payload containing ordered
  parent lineage, named input SHA-256 fingerprints, transform name/version,
  normalized parameters, ordered output schema, row-order semantics, and research
  context. It rejects ambiguous or non-finite values and seals its payload against
  mutation of caller-owned dictionaries. Strict identity snapshots are now stored
  in generation manifests and atomically indexed by node key. Lookup reconstructs
  and re-hashes the identity, leases the generation, and fails closed on corruption.
  The writer lock deduplicates concurrent same-node publication, while garbage
  collection preserves indexed nodes. Factor-execution integration is next.
- [x] Prototype page-aligned immutable `float64` column segments in an NVMe-backed `mmap`, with a Python format oracle.
- [ ] Define segment headers, checksums, generations, leases, and crash recovery.
  Immutable v1 segments now have atomic generation publication, strict manifests,
  documented crash states, durable reader leases, conservative locked garbage
  collection, backward-compatible v2 chunked lazy verification, and forced-process-
  termination coverage at the column, manifest, generation-rename, and pointer-
  replacement boundaries. A true cross-process reader/collector test proves that
  a live lease prevents deletion. A writer-lifecycle lock now lets collection
  reclaim abandoned staging directories and pointer files without racing a live
  writer. Cross-host lease coordination and cryptographic/authenticated integrity
  remain open.
- [ ] Prototype descriptor-only persistence transport with a blocking reference implementation. A separate in-memory tick SPSC prototype now exists; factor-cache baselines still do not justify using a ring for persistence.
- [x] Benchmark the in-memory tick prototype against per-tick and batched Python bounded queues; copied native batches beat per-tick handoff but lose materially to passing NumPy batch views.
- [x] Add an in-place C++ tick-factor consumer. It removes the outbound copy and materially improves native throughput, but still trails vectorized NumPy batches on the initial workload.
- [ ] Benchmark native atomic spin/backoff/park behavior against a bounded blocking queue.
  A first repeated matrix on Apple Silicon covers batch sizes 64/256/1024 and
  spin budgets 0/64/256/1024. It favors the in-place native consumer for modest
  batches, while vectorized Python/NumPy wins at 1024; controlled-core latency
  and explicit park-timeout sweeps remain open.
- [ ] Add zero-copy NumPy/Arrow/Polars views with explicit lifetime protection.
- [ ] Add capacity limits, eviction, compaction, observability, and NVMe-wear metrics.
- [x] Add exact cross-format equality checks, host-visible file/allocation
  amplification, cache-device metadata, and advisory page-cache-eviction reads
  to the factor-cache benchmark. SSD-controller/NAND write amplification still
  requires device telemetry and is explicitly reported as unmeasured.
- [x] Run sanitizer, concurrency, fault-injection, and forced-process-termination tests.
  ASan/UBSan/LeakSan and ThreadSanitizer run in Linux CI; publication crash tests
  and cross-process lease/garbage-collection tests now run in the Python suite.
- [x] Record the initial benchmark decision: use mapped Polars IPC as the baseline and defer the native ring until descriptor coordination is shown to be a bottleneck.

Exit criteria: cached and uncached factor trees produce identical schemas, values,
nulls, and ordering; interrupted writes cannot become visible; resource usage is
bounded; and representative end-to-end workloads show a material improvement over
the simplest correct baseline.

## Required adversarial test matrix

| Surface | Cases that must be covered |
| --- | --- |
| Time | Empty/single timestamp, duplicates, descending input, gaps, intraday/day boundaries, DST/time zones, differing NumPy units |
| Numeric | NaN, ±Inf, signed zero, tiny/huge values, overflow, float tolerance, zero/negative equity |
| Trading | Partial fill, reject/cancel, FOK/DAY, lag 0/>0, long/short, flip through zero, multiple fills at one timestamp |
| Instruments | Multiple symbols/group, symbol reuse, rolls, multipliers, option expiry, missing/stale marks |
| Reporting | Every start/end boundary, empty selection, one group/all groups, reconciliation across all exported frames |
| Optimization | Min/max ordering, ties, invalid results, adaptive generator, worker crash, unpicklable callback, interruption |
| Files | Empty, truncated, oversized, decompression bomb, invalid encoding/dtype, uneven columns, concurrent access |
| Mapped factor cache | Cold/warm mapping, page faults, truncated segments, torn publication, checksum mismatch, stale generation, eviction while leased, process crash/restart |
| Ring/concurrency | Empty/full/wraparound, cursor overflow, false sharing, slow producer/consumer, thread/process contention, bounded spin then park, cancellation |
| Factor DAG | Shared ancestors, branching, deterministic keys, code/config/schema changes, null/order preservation, cache poisoning, partial invalidation |
| Platform | Python support matrix, Linux/macOS/Windows, source build, wheel build, no-Conda build, optional extras absent |

## Validation gates

- Zero unresolved P0 findings.
- 100% branch coverage on ledger/order-state transitions and date/group filtering; set a pragmatic repository-wide threshold separately.
- Cython/native calculations match the independent oracle within documented tolerances.
- Property tests run with saved failure seeds and a meaningful case count in CI.
- Sanitizers and a bounded fuzz smoke test pass on every native change.
- Clean build, wheel install, `import gambit`, and minimal-submodule imports pass on supported platforms.
- Examples/notebooks execute against pinned fixtures without network access.
- Performance baselines detect material regressions but never replace correctness assertions.
- NVMe cache benchmarks report end-to-end factor-tree gains, CPU consumption, page
  faults, resident memory, and physical bytes written; ring throughput alone is not
  an acceptance metric.

## Deferred design decisions

These require maintainer intent and should be recorded as architecture decisions before implementation:

- Whether contract identity is global by symbol or scoped by contract group/exchange/expiry.
- Whether equal-timestamp event order is insertion order, rule order, or an explicit priority.
- Whether adaptive optimizers are supported in parallel mode.
- How missing marks, stale prices, expiry, corporate actions, cash, margin, and multi-currency accounting should behave.
- Whether notebooks or Python modules are the canonical source for generated code.
- Which dependencies and Python/platform versions are genuinely supported for version 1.x.
- Whether the mapped cache is single-process, process-shared, or both, and which
  memory-ordering/durability contract it guarantees.
- Whether the initial column layout should be Arrow-compatible raw buffers, Arrow
  IPC, or another format after measurement of zero-copy use, recovery, and evolution.
- What improvement threshold justifies native spin/backoff/park synchronization
  over a simpler blocking bounded queue.

## Immediate performance research change set

Keep the first cache experiment intentionally non-production:

1. Define two representative factor DAG fixtures and record uncached Polars baselines.
2. Write the mapped-segment format and publication/recovery invariants as an architecture decision.
3. Prototype one fixed-width column type with a descriptor-only SPSC ring and a blocking queue oracle.
4. Add equality, crash-recovery, resource-bound, and benchmark tests before expanding dtype or concurrency support.

Do not combine this experiment with production cache integration. Preserve a
reviewable causal link between the mapped layout, synchronization choice,
correctness tests, and measured factor-tree performance.
