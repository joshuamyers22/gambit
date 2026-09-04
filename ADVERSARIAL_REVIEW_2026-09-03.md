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
| Tests | `uv run pytest --cov=gambit --cov-report=term-missing` | Pass | 551 passed; native tests executed locally |
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

## Improvement order

1. Classify and test or deprecate the low-coverage legacy modules.

## Release boundary

The current evidence supports market, limit, VWAP, atomic market-roll, accounting, risk, and factor-cache research workflows covered by the 551-test suite. Stop-limit orders are retained only as a deprecated compatibility type and are deliberately rejected before execution.
