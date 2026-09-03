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
| Tests | `uv run pytest --cov=gambit --cov-report=term-missing` | Pass | 523 passed; native tests executed locally |
| Coverage | same command | Pass, uneven | 78% total; `markets.py` improved from 0% to 72%, `holiday_calendars.py` from 21% to 50%, and `optimize.py` from 36% to 40%; important weak areas still include `pq_utils.py` 38% |
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
- Correction: identify supported versus compatibility-only modules, deprecate unused surfaces, and enforce module-specific floors for supported policy rather than chasing uniform aggregate coverage.
- Acceptance: CI fails when supported optimizer/calendar/market contracts lose coverage; compatibility-only modules have explicit ownership/deprecation status.

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

## Improvement order

1. Classify and test or deprecate the low-coverage legacy modules.

## Release boundary

The current evidence supports market, limit, VWAP, atomic market-roll, accounting, risk, and factor-cache research workflows covered by the 523-test suite. Stop-limit orders are retained only as a deprecated compatibility type and are deliberately rejected before execution.
