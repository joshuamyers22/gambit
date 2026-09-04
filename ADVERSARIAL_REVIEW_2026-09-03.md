# Gambit Adversarial Review — Current Tree

## Review metadata

- Repository: `gambit`
- Remote: `https://github.com/joshuamyers22/gambit.git`
- Branch and commit: `main` at `cf8f59e8a3311d8902258304b230fc07bfd0a773`
- Review date: 2026-09-03
- Product purpose: quantitative strategy backtesting, execution simulation, accounting, risk, and persistent factor computation
- Runtime: Python 3.10–3.12 with Cython/C++ native extensions
- Pre-existing working-tree changes: none
- Exclusions: live market integrations and Linux-only sanitizer execution were not reproduced locally

## Executive verdict

- Overall grade: B
- Release recommendation: approve covered market/limit/VWAP and atomic market-roll research workflows with follow-up; stop-limit execution is explicitly unsupported
- Highest risk: low-coverage legacy policy and a dependency audit contaminated by obsolete local package metadata
- Strongest property: extensive adversarial tests now exercise transactional rollback, P&L reconciliation, native storage, cross-process leases, order lifecycle, risk, and factor identity
- First improvement: make dependency auditing reproducible and classify the remaining low-coverage legacy modules

## Verification evidence

| Check | Command | Result | Notes |
|---|---|---|---|
| Lint | `uv run ruff check src tests` | Pass | no findings |
| Static typing | `uv run mypy` | Pass | 49 configured source files |
| Tests | `uv run pytest --cov=gambit --cov-report=term-missing` | Pass | 557 passed; native tests executed locally |
| Coverage | same command | Pass, uneven | 79% total; `markets.py` improved from 0% to 79%, `holiday_calendars.py` from 21% to 54%, `optimize.py` from 36% to 60%, and `pq_utils.py` from 38% to 47% |
| Policy coverage | `python tools/check_coverage_policy.py` | Pass | protected unit-suite floors: markets 75%, calendars 50%, optimizer 55%, utilities 35% |
| Build | `uv build` | Pass | native macOS ARM64 wheel and sdist built |
| Lock reproducibility | initial `make check` | Fail, corrected locally | `uv run` regenerated stale project metadata in `uv.lock`; updated lock now passes `uv lock --check` |
| Dependency audit | `make audit` | Pass | exact hashed runtime set exported from `uv.lock`; no known vulnerabilities |

## Architecture map

- Core entities and mutable lifecycle: `pq_types.py`, `instruments.py`, `account.py`, `contract_pnl.py`.
- Strategy orchestration: `strategy.py`, `stages.py`, `strategy_builder.py`, extracted validation/input/callback modules.
- Execution policy: `strategy_components.py`, `execution_costs.py`.
- Analytics and risk: evaluator, portfolio, covariance, VaR, position sizing, and reporting modules.
- Persistence and native infrastructure: `pq_io.py`, `factor_store.py`, factor DAG/cache modules, Cython P&L, C++ CSV/options/mapped storage.
- Delivery: Python public API plus `gambit-factor-cache` CLI.

## Findings

### [Resolved] Public stop-limit orders were silently ignored

- Location: `src/gambit/strategy_components.py:102-107`; exported from `src/gambit/__init__.py`; defined at `src/gambit/pq_types.py:545-572`
- Evidence: `SimpleMarketSimulator` explicitly processes only `MarketOrder` and `LimitOrder`, silently continuing for every other order. No other simulator or production path references `StopLimitOrder`; only constructor-validation tests do. The source still says `TODO: code for stop orders`.
- Failure mode: a strategy can create a valid public `StopLimitOrder`, receive no exception, and never receive the expected execution. Depending on time-in-force it can later be cancelled, producing a plausible backtest that omitted protective or entry trades.
- Test protection: tests verify trigger/limit field construction but never execute a stop order end to end.
- Correction implemented: construction emits `DeprecationWarning`; rule validation and the built-in simulator reject the type with actionable instructions to emit market/limit orders from an explicit trigger rule.
- Verification: regression coverage proves the deprecated type fails at the rule boundary rather than disappearing from a run.

### [Resolved] RollOrder bypassed base order invariants and had no execution path

- Location: `src/gambit/pq_types.py:527-542`
- Evidence: `RollOrder.__post_init__` does not call `Order.__post_init__`. It validates only `close_qty` and `reopen_qty`, leaving contract, timestamp, base quantity, status, and time-in-force unchecked. No production simulator or test uses the type, although it is exported publicly.
- Failure mode: malformed roll orders can be constructed, and valid-looking roll orders are silently ignored by available simulators. A futures roll can therefore disappear from a backtest without a failure signal.
- Test protection: none.
- Correction implemented: a roll is now a validated command naming distinct outgoing and incoming contracts in the same group, with signed opposite-side quantities. Rule validation expands it into linked market legs so existing risk, execution, accounting, and reporting paths inspect both legs. The built-in simulator fills both linked legs or neither when either price is unavailable.
- Verification: tests cover invalid identity/direction, expansion metadata, all-or-neither missing-price behavior, successful two-leg execution, and per-contract accounting.

### [Resolved] The committed dependency lock was stale

- Location: `pyproject.toml`, `uv.lock`, `Makefile:3-11`
- Evidence: the first `make check` changed `uv.lock`: project version moved from 1.0.0 to 1.1.0 and the recently split optional-dependency groups were regenerated. A supposedly read-only quality gate therefore dirtied a clean checkout.
- Failure mode: `uv sync --frozen --all-extras` fails in a clean environment/CI, while plain `uv run` silently repairs developer state and obscures the drift.
- Correction made locally: regenerated `uv.lock`, added a local `make lock` prerequisite, and added a dedicated CI lock job; `uv lock --check` now passes.
- Acceptance: CI runs `uv lock --check` before any command capable of updating the environment, and the corrected lock is committed with `pyproject.toml` changes.

### [Resolved] Dependency audit was not reproducible from the working tree

- Location: ignored local `src/gambit.egg-info`; `Makefile`; CI workflow
- Evidence: `pip-audit --strict` fails because Python discovers obsolete `gambit==1.0.2` metadata alongside `gambit-markets==1.1.0`. The standard `make check` contains no dependency-audit target.
- Failure mode: developers cannot distinguish a vulnerable dependency result from contaminated local metadata, and the primary gate provides no current vulnerability evidence.
- Correction implemented: `make audit` exports the exact hashed runtime set from `uv.lock` and audits it without inspecting or installing the contaminated environment. A dedicated CI job runs the same pinned audit tool and command.
- Verification: the command succeeds from the existing built working tree with no known vulnerabilities.

### [Medium] Passing aggregate coverage masks unverified legacy policy

- Location: `src/gambit/markets.py`, `holiday_calendars.py`, `optimize.py`, `pq_utils.py`; `Makefile:9-10`
- Evidence: total coverage is 77%, but entire or major policy-heavy modules remain at 0–38%. No minimum threshold is passed to pytest-cov.
- Failure mode: changes to optimizer selection/feedback, calendars, legacy contract helpers, or shared numerical utilities can regress while the headline suite remains green.
- Correction implemented: the full local gate and cross-platform unit CI matrix enforce explicit floors for supported market (75%), calendar (50%), optimizer (55%), and numerical utility (35%) modules. The checker fails closed when coverage data or a named module is unavailable.
- Verification: current unit-suite measurements are 79%, 54%, 60%, and 38%, respectively. The policy and its maintenance rule are documented in the testing guide.

### [Resolved] One-digit contract years silently selected the wrong decade

- Location: `src/gambit/markets.py`
- Evidence: the previous pivot decoded `ESZ6` as December 2016 after 2024, and a typo in previous-symbol rollover produced `ESZ-1` instead of December 2019.
- Failure mode: expiry, roll selection, and post-expiry P&L could use a contract ten years away while returning plausible timestamps.
- Correction implemented: deterministic repository nomenclature maps one-digit years to standard 2020–2029 symbols and requires two-digit years for other decades. Thus `ESZ6` is 2026 and historical `ESZ16` is 2016. Futures navigation parses the full symbol and emits a two-digit year when crossing outside the current supported decade. E-mini option decoding follows the same exact policy and rejects malformed shapes.
- Verification: 14 focused tests cover current/historical expiry, decade-boundary navigation, option decoding, and malformed symbols.

### [Resolved] Single-process optimization skipped alternating suggestions

- Location: `src/gambit/optimize.py:117-129`
- Evidence: the optimizer iterated a generator with a `for` loop and then called `send()` after each cost calculation. `send()` resumes the generator and returns its next yielded suggestion, but that return value was discarded; the following loop iteration resumed the generator again.
- Failure mode: ordinary and adaptive generator sources silently omitted alternating parameter combinations, producing plausible but incomplete experiment rankings.
- Correction implemented: the single-process driver now advances explicitly, records the completed experiment before feedback, and uses the value returned by `send()` as the next suggestion.
- Verification: regression tests require all values from both ordinary and feedback-consuming generators to execute in sequence and require every adaptive result to be received.

### [Resolved] Optimizer boundary and reporting inputs failed inconsistently

- Location: `src/gambit/optimize.py:111-114`, `src/gambit/optimize.py:208-219`
- Evidence: `max_pending_tasks=0` was replaced by the default because configuration used truthiness before validating positivity. Separately, dataframe construction discovered the union of auxiliary-cost keys but directly indexed every result by every key.
- Failure mode: an explicitly invalid concurrency bound silently changed behavior, while conditional auxiliary metrics raised `KeyError` during otherwise valid result reporting.
- Correction implemented: explicit zero and negative task bounds fail validation; missing auxiliary metrics are represented by `NaN` in the stable union of columns.
- Verification: regression tests cover the rejected zero boundary and sparse auxiliary-cost dataframe output.

### [Resolved] Utility edge cases ignored explicit caller intent

- Location: `src/gambit/pq_utils.py:33-60`, `src/gambit/pq_utils.py:815-823`
- Evidence: a zero shift assigned an empty slice into the full output and raised a broadcast error. Recursive lookup accepted a directory argument but always searched the process working directory; multiple matches also depended on filesystem traversal order.
- Failure mode: a no-op transformation crashed, while data-loading examples could select a same-named file outside the requested data root or select different files across filesystems.
- Correction implemented: zero shifts return an independent unchanged array, and recursive lookup searches only the supplied root and sorts matches before selecting the first.
- Verification: regression tests cover zero-shift values and ownership, requested-root isolation, stable match ordering, and missing roots.

### [Resolved] Array utilities failed on valid dtype and cardinality boundaries

- Location: `src/gambit/pq_utils.py:33-75`, `src/gambit/pq_utils.py:181-198`
- Evidence: the default shift fill was `NaN` for every non-boolean dtype, which cannot be stored in integer, datetime, timedelta, or string arrays. Closest-value lookup indexed a nonexistent second element for singleton inputs and failed opaquely for empty inputs.
- Failure mode: common lagged integer/date signals and one-point reference grids raised low-level NumPy errors instead of returning defined results.
- Correction implemented: shift fill values now follow the destination dtype, singleton closest-value queries always resolve to index zero, and empty reference arrays raise an explicit `ValueError`.
- Verification: parameterized regressions cover integer, datetime, and string shifts plus scalar/vector singleton queries and empty references.

### [Resolved] Weekly option expiry could cross into the next contract month

- Location: `src/gambit/holiday_calendars.py:283-294`, `src/gambit/markets.py:196-220`
- Evidence: requesting a fifth weekday in a month containing only four used `relativedelta` to return the first matching weekday of the next month. Invalid weekday and week coordinates were also accepted until a lower-level operation happened to fail.
- Failure mode: a syntactically valid weekly option such as `E5AG21` was assigned a March 2021 expiry even though its symbol names February, contaminating contract selection and expiration P&L with plausible dates.
- Correction implemented: weekday/week coordinates are validated and positive occurrences must remain inside the requested month. The explicit `week=-1` end-of-month convention is retained and documented.
- Verification: tests cover an existing fifth weekday, a nonexistent fifth weekday through both calendar and option APIs, invalid coordinates, and weekend month-end rollback.

### [Resolved] Numerical utility boundaries returned plausible invalid results

- Location: `src/gambit/pq_utils.py:198-267`, `src/gambit/pq_utils.py:499-523`
- Evidence: a zero rounding increment returned `NaN` with only a runtime warning; zero and oversized rolling windows produced empty arrays with nonsensical shapes; empty, duplicate, or descending bucket boundaries were accepted or failed incidentally; a singleton frequency series reached `argmax` on an empty array.
- Failure mode: invalid configuration could propagate empty or `NaN` signals into research outputs, while underdetermined sampling frequency failed with an implementation-detail exception.
- Correction implemented: these public utilities now validate window sizes, positive finite increments, strictly increasing nonempty buckets, and at least two timestamps before calculation.
- Verification: boundary regressions cover zero, negative, non-finite, oversized, duplicate, descending, empty, and singleton inputs.

### [Resolved] Optimizer plots crashed or omitted conditional metrics

- Location: `src/gambit/optimize.py:223-425`
- Evidence: 2D plotting checked only whether the unfiltered experiment list was empty, then indexed the first valid result even when every result was non-finite. Plot limits could similarly empty a 3D dataset before reductions. The 2D `all` view read auxiliary keys only from the first experiment.
- Failure mode: completed optimization runs could fail during reporting, or silently omit metrics emitted only by later/conditional evaluations.
- Correction implemented: plotting exits cleanly when validation or limits remove every result, and the 2D all-metrics view uses the stable union of auxiliary keys with `NaN` for missing observations.
- Verification: regression coverage exercises all-invalid 2D data, fully filtered 3D data, and sparse auxiliary metrics whose keys first appear in different experiments.

### [Resolved] Custom resampling functions silently discarded columns

- Location: `src/gambit/pq_utils.py:394-462`
- Evidence: `resample_trade_bars` accepted a mapping of custom aggregation callables, excluded those columns from standard aggregation, and never invoked the callables. Missing time/custom columns and unequal date/value arrays were left to fail inside Polars.
- Failure mode: caller-defined signals disappeared during downsampling without an error, allowing strategies to consume incomplete frames that otherwise looked valid.
- Correction implemented: custom callables now receive a Polars column expression and their aggregate expressions are included in output; custom VWAP can explicitly override the built-in weighted calculation. Required schema and paired lengths fail at the public boundary.
- Verification: tests prove custom mean and VWAP override execution, reject absent time/custom columns, and reject unequal series lengths.

### [Resolved] Duplicate CI job key disabled the reproducible dependency audit

- Location: `.github/workflows/ci.yml`; `tests/test_architecture.py`
- Evidence: the workflow contained two top-level jobs named `dependency-audit`. Standard YAML mapping semantics retain the later value, so GitHub Actions saw only the legacy job that installed unpinned latest tooling and audited the project declaration instead of the exact lock export.
- Failure mode: CI appeared to contain the pinned lock-based audit while silently executing a materially different, mutable check.
- Correction implemented: the shadowing legacy job was removed. A repository architecture test parses every workflow with a safe loader that rejects duplicate mapping keys at any depth.
- Verification: all workflows load without duplicate keys, and the surviving dependency-audit job uses pinned `setup-uv`, `pip-audit==2.10.1`, `uv export --frozen`, and `--no-deps`.

### [Resolved] Return normalization and three-year windows corrupted edge cases

- Location: `src/gambit/evaluator.py:261-288`, `src/gambit/evaluator.py:420-457`
- Evidence: `np.nan_to_num` was called without infinity replacements even though the public contract says all non-finite returns become zero; NumPy therefore converted infinities to maximum finite floats. Three-year cutoffs used `datetime.replace(year=...)`, which raises for a February 29 endpoint whose target year is not leap, and date/return windows disagreed on cutoff inclusion.
- Failure mode: one infinite observation could create overflowed equity and meaningless ratios, while valid leap-day histories crashed during standard reporting.
- Correction implemented: finite observations now define the leading-data boundary, all requested non-finite replacements explicitly use zero, and calendar-aware three-year subtraction includes the exact cutoff consistently.
- Verification: regressions cover leading/subsequent positive infinity, negative infinity and `NaN`, plus a February 29 endpoint across dates, returns, and rolling drawdown windows.

### [Resolved] Calendar-period returns used trading-day annualization

- Location: `src/gambit/evaluator.py:23-47`; `src/gambit/pq_utils.py:499-526`
- Evidence: frequency inference represented one calendar month as 30 fractional days, after which `compute_periods_per_year` divided the 252-trading-day constant by 30. Regular monthly observations therefore reported 8.4 periods per year; two-month observations reported 4.2.
- Failure mode: geometric return, volatility ratios, and other annualized metrics were systematically understated or distorted for monthly and multi-month research series while remaining numerically plausible.
- Correction implemented: histories with at least three observations are first tested for a dominant nonzero calendar-month interval and annualized as `12 / months_per_observation`; daily and intraday paths retain their existing trading-period conventions. Short ambiguous histories continue to require an explicit override when inference is unsuitable.
- Verification: month-end regression series require exactly 12 periods per year for monthly data and 6 for two-month data.

### [Resolved] All-invalid return histories were reported as flat performance

- Location: `src/gambit/evaluator.py:420-467`, `src/gambit/evaluator.py:500-527`
- Evidence: when no finite return existed, the leading-data search returned `-1` and skipped trimming. The default subsequent replacement then converted the entire series to zeros even though `leading_non_finite_to_zeros` was false.
- Failure mode: a feed containing only `NaN`/infinite observations produced a plausible flat equity curve and zero-return metrics instead of signaling that no performance data existed.
- Correction implemented: all-invalid histories become empty when leading replacement is disabled, and the public evaluator rejects them explicitly. Callers that deliberately enable leading replacement retain timestamps and receive an all-zero series.
- Verification: regression coverage distinguishes default rejection from explicit zero-fill and validates both returned returns and equity.

### [Resolved] Hosted CI diverged from local typing and notebook evidence

- Location: `src/gambit/pq_utils.py`, `factor_cache.py`, `evaluator.py`; `examples/notebooks/multiple_contracts.ipynb`; GitHub Actions CI
- Evidence: the hosted Python 3.11/3.12 jobs failed on newer NumPy-stub inference for scalar/array unions and structured dtypes even though the locked local mypy run passed. The notebook job failed because overlapping per-contract price windows were concatenated with duplicate timestamps, correctly rejected by the hardened `PriceFuncArrayDict` boundary.
- Failure mode: repeated pushes had green local gates but a red hosted matrix, and the documented multiple-contract workflow was not executable from a clean checkout.
- Correction implemented: unstable NumPy inference points now carry portable explicit annotations. The notebook stably sorts each combined contract series and retains the first observation for duplicate timestamps before constructing its immutable price function.
- Verification: local mypy and the formerly failing notebook execute successfully; final acceptance requires the pushed GitHub matrix to pass.

### [Resolved] Missing drawdowns and plotting leaked implementation behavior

- Location: `src/gambit/evaluator.py:225-250`, `src/gambit/evaluator.py:584-739`, `src/gambit/optimize.py:319-333`
- Evidence: maximum-drawdown date used `argmax` directly, so a leading `NaN` could be selected instead of the largest finite drawdown; all-missing percentage calculation emitted a runtime warning. Return and optimizer plots also printed internal extrema/dates to stdout unconditionally.
- Failure mode: direct reporting helpers could identify the wrong drawdown date, and library plotting polluted logs, notebooks, and machine-readable command output.
- Correction implemented: extrema operate only on finite observations and return `NaN`/`NaT` when none exist; NumPy floating scalars are formatted consistently; debug prints were removed.
- Verification: regressions cover partial/all-missing drawdowns and require optimizer plotting to leave stdout untouched.

### [Resolved] Percentile ranking was undefined at common boundaries

- Location: `src/gambit/pq_utils.py:365-380`
- Evidence: singleton arrays divided by zero, duplicate observations received different ranks based on their sort position, and multidimensional or non-finite arrays were accepted despite having no defined ordering contract.
- Failure mode: a constant one-observation signal crashed under the repository's strict NumPy error mode, while identical scores could drive different decisions.
- Correction implemented: ranking is explicitly one-dimensional and finite, singleton rank is zero, and ties receive their deterministic average rank using a stable sort.
- Verification: regressions cover singleton, tied, multidimensional, `NaN`, and infinite inputs.

### [Resolved] Concurrent CSV exports shared one staging filename

- Location: `src/gambit/pq_utils.py:580-599`
- Evidence: every export to a destination wrote through the literal `<destination>.tmp` path before renaming it, with no cleanup on serialization failure.
- Failure mode: concurrent writers could replace or rename each other's partial content, a stale temporary file could be overwritten, and failed serialization left ambiguous debris beside the intended output.
- Correction implemented: each export now uses a unique temporary file in the destination directory, atomically replaces the target only after serialization succeeds, and removes staging content on every exit path.
- Verification: regressions preserve an unrelated legacy `.tmp` file, validate completed content, and require failed serialization to leave neither a target nor staging files.

### [Resolved] Interactive confidence intervals were inverted

- Location: `src/gambit/interactive_plot.py:182-184`
- Evidence: `bootstrap_ci` returns `(lower, upper)`, but the plotting statistic assigned that tuple to `(ci_up, ci_down)` and then emitted the upper value in the `ci_d_*` column and the lower value in `ci_u_*`.
- Failure mode: confidence-band consumers received a lower boundary greater than the upper boundary, producing inverted or malformed uncertainty plots while the central estimate remained plausible.
- Correction implemented: tuple assignment now follows the bootstrap API and emits lower then upper values under the matching column names.
- Verification: a deterministic regression injects known bounds and requires `ci_d_95=1` and `ci_u_95=9`.

### [Resolved] Confidence bands ignored the configured statistic

- Location: `src/gambit/interactive_plot.py:152-186`; `src/gambit/pq_utils.py:739-770`
- Evidence: `MeanWithCI` used `mean_func` for the center but always called `bootstrap_ci` with its default arithmetic mean. Invalid confidence levels and iteration counts reached percentile indexing, and sampling exposed no local reproducibility control.
- Failure mode: median or custom-statistic plots combined one estimator with another estimator's interval, while malformed settings failed with incidental index errors or yielded meaningless bands.
- Correction implemented: the configured statistic is forwarded to bootstrap sampling, confidence levels and iteration counts are validated at their public boundaries, and callers may supply a seeded NumPy generator.
- Verification: tests require estimator identity forwarding, repeatable seeded intervals, and explicit rejection of empty/multidimensional samples and invalid confidence or iteration settings.

### [Resolved] Percentile bucketing converted missing values to zero

- Location: `src/gambit/interactive_plot.py:84-105`
- Evidence: observations unmatched by finite bucket conditions used `np.select`'s implicit numeric-zero default. Bucket counts of zero, negative values, booleans, or values not dividing 100 also produced division/step errors or a different number of buckets than requested.
- Failure mode: missing plot observations appeared as real zero-valued statistics, and configuration could silently change requested quantile resolution.
- Correction implemented: finite data defines exactly `n` evenly spaced percentile buckets, unmatched observations remain `NaN`, all-invalid inputs return all missing, and dimensions/counts are validated explicitly.
- Verification: regressions cover mixed and wholly non-finite arrays, invalid counts, and multidimensional input.

### [Resolved] Interactive plotting extra omitted a required runtime

- Location: `pyproject.toml`; `src/gambit/interactive_plot.py:347`
- Evidence: a clean development/notebook installation raised Plotly's `ImportError: Please install anywidget to use the FigureWidget class` on the first `LineGraphWithDetailDisplay` call. None of the visualization-bearing extras declared `anywidget`.
- Failure mode: the public interactive plotting API imported successfully but could not construct any plot in the documented environment.
- Correction implemented: `anywidget>=0.9.13` is included in visualization, notebooks, all, development, and documentation extras while remaining absent from the core runtime dependency set.
- Verification: the locked development environment constructs the Plotly figure widget, and minimal-root import coverage explicitly blocks `anywidget` to preserve optional dependency isolation.

### [Resolved] The eleventh interactive series crashed palette selection

- Location: `src/gambit/interactive_plot.py:361`
- Evidence: default color selection indexed a fixed ten-entry palette directly with the line number. Any plot containing eleven or more unconfigured series raised `IndexError` before rendering.
- Failure mode: valid higher-cardinality strategy comparisons failed only at presentation time, after their statistics had already been computed.
- Correction implemented: default palette selection wraps by palette length, retaining deterministic input-order color assignment for arbitrary series counts.
- Verification: an 11-series figure regression renders every trace and requires series 11 to reuse series 1's color.

## Improvement order

1. Classify and test or deprecate the low-coverage legacy modules.

## Release boundary

The current evidence supports market, limit, VWAP, atomic market-roll, accounting, risk, and factor-cache research workflows covered by the 557-test suite. Stop-limit orders are retained only as a deprecated compatibility type and are deliberately rejected before execution.
