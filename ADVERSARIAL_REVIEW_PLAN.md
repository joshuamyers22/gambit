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

The C++ parser performs manual buffer management, delimiter mutation, dtype dispatch, raw allocation, and Python/NumPy ownership transfer. Static review did not prove an exploitable defect, but malformed, huge, truncated, quoted, encoding-invalid, or decompression-heavy inputs are an important attack and crash surface.

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
- [ ] Add golden end-to-end strategy scenarios covering market-simulation behavior.

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
- [ ] Enable ASan/UBSan native test jobs and compiler warnings-as-errors for project-owned C/C++.
- [ ] Add resource limits and malformed-input tests.
- [ ] Make HDF5 writes schema-versioned and as crash-safe as documented.
- [ ] Verify Cython/native results against independent reference implementations.

Exit criteria: fuzz smoke runs and sanitizers are clean, corpus regressions are checked in, and ownership/resource limits are documented.

### Phase 4 — API, documentation, and release quality

- [ ] Establish explicit exports, semantic versioning, deprecation, and changelog policy.
- [ ] Separate unit, integration, native, notebook, plotting, and performance suites.
- [ ] Add architecture diagrams and document accounting/execution assumptions.
- [ ] Automate docs/notebook regeneration checks.
- [ ] Build and install wheels in clean environments before release.

Exit criteria: public API and financial assumptions are documented, generated artifacts cannot silently drift, and release artifacts install and import on every supported platform.

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

## Deferred design decisions

These require maintainer intent and should be recorded as architecture decisions before implementation:

- Whether contract identity is global by symbol or scoped by contract group/exchange/expiry.
- Whether equal-timestamp event order is insertion order, rule order, or an explicit priority.
- Whether adaptive optimizers are supported in parallel mode.
- How missing marks, stale prices, expiry, corporate actions, cash, margin, and multi-currency accounting should behave.
- Whether notebooks or Python modules are the canonical source for generated code.
- Which dependencies and Python/platform versions are genuinely supported for version 1.x.

## Immediate next change set

Keep the first pull request intentionally small:

1. Create a conventional `tests/` layout and narrow pytest discovery.
2. Add regression tests for the four confirmed silent-result defects.
3. Fix only those four defects.
4. Add account-level reconciliation assertions and run them against existing example data.
5. Document behavior changes prominently because consumers may have unknowingly depended on incorrect filtered/ranked output.

Do not combine the first correctness patch with broad API, formatting, or packaging rewrites; preserving a reviewable causal link between each regression test and fix is especially important for financial code.
