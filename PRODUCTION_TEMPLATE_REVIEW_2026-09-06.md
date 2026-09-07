# Gambit production-template review — 2026-09-06

## Review metadata

- Repository: gambit; origin https://github.com/joshuamyers22/gambit.git.
- Reviewed branch/commit: main, 48f9996a3a3326bda96d0579f9040bd73ab9aad3.
- Reference: production-project-template at e132c6e1f1844f1112d2aa69d5ca045422b8a5cd; repository standard, adversarial review template/playbook, Python guide, C++ latency guide, and release checklist. Also read the canonical Dropbox repository standard referenced by ~/Projects/PRODUCTION_REPOSITORY_STANDARD.md.
- Reviewer: Codex. Scope: Python backtesting/accounting/risk, factor persistence, experimental native replay, tests, documentation, CI, packaging, and release controls.
- Runtime: CPython 3.10–3.12; Python/Cython/C++11 library with an operational factor-cache CLI. Local verification used macOS ARM64 and Python 3.10.20.
- Pre-existing tracked/untracked changes: none. Implementation was not changed during this review.
- Exclusions: vendored option-pricing mathematics and generated C were not independently audited; Linux sanitizers, other platform/Python combinations, notebook execution, hosted GitHub settings, PyPI/TestPyPI publishing, and full-volume performance reruns were not performed.

## Executive verdict

**Release recommendation: block a general production release until findings 1–4 are addressed or the affected APIs are explicitly removed from its supported scope.** The code has a strong verification foundation, but the public entry helpers can produce incorrectly sized or incomplete orders while all 772 existing tests pass. Publishing also lacks an enforced dependency on the complete quality gate.

Overall assessment: substantial production infrastructure, with remaining correctness and delivery gaps. The strongest properties are deterministic regression/oracle tests, transactional accounting protection, explicit experimental-native limitations, immutable result snapshots, native sanitizers in CI, and installed-package verification.

First improvement: add behavioral regressions for contract multipliers and multi-contract VWAP entry, then correct those helpers. A wholesale architecture rewrite would not address the highest risks efficiently.

## Verification evidence

A clean local clone was created at /private/tmp/gambit-review-clean-20260906. Logs and build artifacts under /private/tmp are temporary review evidence, not release artifacts.

| Check | Command / procedure | Result |
|---|---|---|
| Frozen clean installation | uv sync --frozen --all-extras in clean clone | Pass after allowing dependency downloads; 146 packages installed |
| Lock | uv lock --check --offline | Pass; 173 packages resolved |
| Lint | .venv/bin/python -m ruff check src tests tools; repeated by clean make check | Pass |
| Typing | .venv/bin/python -m mypy; repeated by clean make check | Pass; 50 configured source files |
| Full suite | .venv/bin/python -m pytest --cov=gambit --cov-report=term-missing --cov-report=json:/private/tmp/gambit-review-coverage-20260906.json | 772 passed in 11.68 seconds in existing environment |
| Clean full suite | make check, through its test step | 772 passed in 31.47 seconds; includes unit/integration/native/fuzz/performance-marked tests |
| Coverage | tools/check_coverage_policy.py | Pass; 83% total Python statement coverage. Five configured module floors pass. This does not measure C++/Cython coverage |
| Complete local command | make check in clean clone | Lock/lint/type/tests/coverage passed; final build was blocked by sandbox DNS. Its exact uv build step was rerun with approved network access and passed. Do not interpret the initial command's exit code as a source defect or a wholly successful single invocation |
| Distribution | uv build --offline --out-dir /private/tmp/gambit-review-build-20260906; clean uv build | Wheel and sdist build successfully |
| Artifact inspection | python -m twine check on both distributions; python tools/verify_release_artifacts.py /private/tmp/gambit-review-build-20260906 | Pass for the local wheel/sdist; not the hosted nine-wheel matrix |
| Installed wheel | Separate venv, hash-pinned core dependencies exported from uv.lock, locally built wheel installed with --no-deps; imports/version/native constructor/CLI checked from /private/tmp | Pass; import path confirmed outside source checkout |
| Security audit | make audit, with approved network access | No known vulnerabilities found in the seven exported core packages; optional extras and bundled native libraries are outside this audit |
| Documentation | Clean python -m sphinx -W --keep-going -b html documentation/source /private/tmp/gambit-review-docs-20260906 | Pass |
| Notebook source hygiene | python tools/check_notebook_cleanliness.py; python tools/check_clean_paths.py documentation/source examples/notebooks | Pass; notebooks were not executed |
| Native warnings | python tools/check_native_warnings.py | Fails after normal frozen setup: pybind11 is missing from the developer environment |
| Native warnings, supplemental | uv run --offline --no-project --with numpy --with pybind11 --python .venv/bin/python python tools/check_native_warnings.py | Pass with an explicitly supplemented ephemeral environment |
| Architecture contracts | Existing nine architecture tests | Pass; import detector has the bypass documented in finding 12 |
| Formatting | No configured format gate executed | Not verified; an explicit opt-out is allowed by the standard |
| Native sanitizers / C++ static analysis | Reviewed CI definitions only | ASan/UBSan and separate SPSC TSan jobs exist; not executed locally. No dedicated static-analysis gate found |
| Performance | Existing benchmark correctness tests run within pytest; reviewed committed raw reports and contracts | Small-corpus tests pass; full-volume figures below are recorded evidence, not new measurements |

Clean-gate log: /private/tmp/gambit-review-make-check-20260906.log. Successful clean build: /private/tmp/gambit-review-clean-build-20260906.log. Sphinx log: /private/tmp/gambit-review-sphinx-20260906.log.

## Architecture map

- Stable contracts: boundaries, calculation, factor_identity, instruments, market_data.
- Domain state: Contract/Order/Trade, Account, ContractPNL, portfolio and risk values.
- Orchestration: Strategy and stages, rule/simulator callbacks, factor DAG and admission decisions.
- Edges: factor CLI, filesystem/mapped factor store, CSV/ZIP/HDF5, Polars persistence, notebook/reporting adapters, C++ and Cython bindings.
- Composition: StrategyBuilder and injected callbacks/policies; the native characterization backtester owns its fixed strategy and portfolio state separately.
- Traced state-changing flow: entry rule → order validation/risk → simulator → trade validation → transactional account update → detached result. Incorrect quantities and omitted orders at the entry boundary remain valid inputs to later layers, explaining why accounting reconciliation alone cannot catch findings 1–2.
- Traced persistence flow: factor identity/DAG → generation store and leases → mapped native columns; existing failure/lease tests provide useful protection. This review did not establish a new persistence corruption defect.

## Findings, ordered by priority

### 1. [High] Entry sizing ignores the contract multiplier

- Location: src/gambit/strategy_components.py:240, :355, :688, :692. Contract multiplier semantics are explicit in src/gambit/pq_types.py:163; NotionalCharge already applies them in src/gambit/execution_costs.py:103.
- Principle: numeric units and correctness at public boundaries.
- Evidence: with equity 100,000, allocation 10%, price 100, and multiplier 50, PercentOfEquityTradingRule returns quantity 100. Actual notional is 500,000 against a requested 10,000; quantity should be 2 under its allocation contract. BracketOrderEntryRule with a 10-point stop returns 1,000 contracts instead of 20 for a 10,000 loss budget. VWAP sizing uses the same price-only denominator.
- Consequence: plausible backtests with exposure and stop-distance risk overstated by the multiplier. Quantity validation and correct P&L accounting do not repair an incorrectly sized order. Custom multiplier-aware rules are mentioned in the guide, but the built-in rules accept these contracts without restriction.
- Test protection: the suite passes; strategy_components.py has 66% statement coverage and the relevant entry-helper paths are largely uncovered.
- Change: apply multiplier-aware monetary sizing and position caps, with explicit price/stop/finite-value validation. If these helpers are intentionally unit-multiplier-only, reject other contracts clearly and document that restriction instead.
- Acceptance: multiplier 1 and 50 fixtures, long/short, allocated multi-contract groups, stop-loss budgets, and position caps reconcile to the requested monetary amount after whole-contract rounding. Scope: medium.

### 2. [High] VWAP entry drops orders and its default configuration crashes

- Location: src/gambit/strategy_components.py:303, :352, :356, :371.
- Principle: stable result cardinality and invalid-state rejection.
- Evidence: the loop accumulates orders but returns [order], so a group containing A and B returns only B. Reproduced with valid stop=90 and price=100. Separately, the default stop_price_ind=None produces NaN, then math.ceil raises ValueError: cannot convert float NaN to integer. An empty group also reaches an unbound final order variable by inspection.
- Consequence: a multi-contract backtest silently omits entries; the ordinary constructor produces an unusable rule. The current suite does not exercise these entry-helper branches.
- Change: return the complete orders collection; define supported no-stop sizing or reject that configuration at construction; validate zero/nonfinite stop distances and empty groups explicitly.
- Acceptance: zero-, one-, and two-contract tests; default configuration has documented behavior; NaN/equal-entry stops fail predictably; both expected symbols reach execution in an end-to-end regression. Scope: small–medium.

### 3. [High] Publishing is not gated on complete quality/security evidence for the release commit

- Location: .github/workflows/release.yml:73, :106, :124; tools/verify_release_installations.py:27.
- Principle: artifact verification and enforced release controls; standard release section.
- Evidence: publishing depends on verify, which depends only on sdist/wheels. This workflow checks metadata and isolated imports but neither runs the full tests/lint/type/audit/sanitizers nor verifies their success for the same commit. Matching a release tag to metadata does not establish those results. RELEASING.md asks maintainers to wait for checks, but that is not a workflow dependency.
- Consequence: a tagged commit with a correctness regression can satisfy the executable publishing graph. Actual environment reviewers and repository protection were not inspected and may add manual controls.
- Change: make the quality workflow reusable or validate required check results for the immutable release SHA; make publication depend on that evidence plus artifact verification. Exercise meaningful native/accounting behavior against installed wheels, beyond imports.
- Acceptance: a deliberately failing required test or audit prevents both publishing paths; all evidence identifies the published SHA. Scope: medium.

### 4. [High] Most CI and native builds bypass the authoritative lock

- Location: .github/workflows/ci.yml:44, :68, :86, :109, :123, :151, :206; .github/workflows/docs.yml:24; pyproject.toml:2; setup.py:18.
- Principle: reproducibility and supply-chain boundaries; standard dependency and C++ build sections.
- Evidence: the separate lock job validates uv.lock, while test/docs jobs run pip install with open dependency ranges. Isolated build requirements are also open ranges and libzip comes from ambient apt/Homebrew state. The core security job audits the lock, not necessarily the packages actually installed by those CI jobs.
- Consequence: the same commit can be tested, audited, and built against different dependency sets. Passing a lock check alone does not establish a frozen install.
- Change: use frozen installs for the reference quality matrix; constrain isolated Python build dependencies; record/pin a reproducible native toolchain/libzip build contract. Keep any intentional newest/minimum-dependency compatibility matrix separate and named.
- Acceptance: CI reports the expected locked versions, rejects stale metadata, and records build dependency/compiler/native-library identity. Broad consumer dependency ranges may remain where compatibility is tested. Scope: medium.

### 5. [Medium] The advertised local quality gate omits maintained checks

- Location: Makefile:20; tools/check_native_warnings.py:14; pyproject.toml development dependencies; tools/check_coverage_policy.py:12.
- Principle: executable local/CI parity.
- Evidence: make check excludes strict documentation, notebook cleanliness/drift, native warnings, and artifact inspection/install smoke. The warning tool cannot import pybind11 even after a clean uv sync --frozen --all-extras because it is declared only as an isolated build requirement. Coverage floors protect five legacy/reporting modules, but not entry execution, accounting, risk, or storage.
- Consequence: the documented complete gate leaves important CI failures and correctness regressions unguarded; the clean-setup native warning failure was reproduced.
- Change: provide one complete offline-capable quality command plus a clearly named network-backed supply-chain/release command; declare required local tooling; include meaningful core policy coverage floors after adding missing behavior tests. Document an explicit formatting policy.
- Acceptance: clean frozen setup can run every documented local check; removing entry-rule regression coverage trips its selected guard; release checks are named and independently runnable. Scope: medium.

### 6. [Medium] Benchmark correctness tests are optional and non-blocking in hosted CI

- Location: tests/conftest.py:39; .github/workflows/performance.yml:3, :8; tests/test_top_of_book_benchmark.py:28.
- Principle: deterministic correctness versus hardware-dependent performance measurements.
- Evidence: *_benchmark.py tests receive only the performance marker unless otherwise classified. Routine CI selections omit them; the performance workflow is manual and continue-on-error. These tests assert reproducible input/result hashes, reconciliation, and harness behavior, not just timing thresholds.
- Consequence: broken replay harnesses and result-identity regressions can merge without executing their regression tests. The local full suite currently catches them.
- Change: run small deterministic benchmark correctness tests as required PR checks; retain timing distributions and large runs as a separate non-blocking/manual job.
- Acceptance: breaking a benchmark result hash or ledger check fails routine CI; noisy host timing does not. Scope: small.

### 7. [Medium] Supply-chain evidence stops at core Python dependencies

- Location: Makefile:8; .github/workflows/ci.yml:23; .github/workflows/release.yml:98.
- Principle: complete dependency and artifact inventory.
- Evidence: the audit export omits optional extras, including persistence/research/notebook dependencies. No release step creates an SBOM or release checksum manifest. Native libraries bundled into repaired wheels are outside the core pip audit.
- Consequence: a clean core audit cannot support a claim that all shipped features and bundled components were assessed.
- Change: audit supported extras and build tooling under explicit policies; emit per-release SBOM and checksums that account for repaired native contents, alongside source/build provenance. Retain Trusted Publishing and its attestation support; this finding does not claim publishing attestations are absent.
- Acceptance: artifacts have a traceable component inventory/checksums and the audit names the supported dependency sets it covers. Scope: medium.

### 8. [Medium] CI token permissions are inherited rather than constrained

- Location: .github/workflows/ci.yml:7; .github/workflows/performance.yml:6.
- Principle: least privilege; repository standard CI section.
- Evidence: these workflows omit permissions; several checkout steps retain credentials. Release and docs already specify read-only contents at workflow level.
- Consequence: CI authority depends on external repository/organization defaults. This is a configuration gap, not evidence that the current token actually has write access.
- Change: specify contents: read and disable checkout credential persistence wherever jobs do not need authenticated Git writes.
- Acceptance: workflow inspection/policy checks reject unintended write permission and credential persistence. Scope: small.

### 9. [Medium] Native replay still needs a completed production acceptance contract

- Location: documentation/architecture/native_tick_backtest.md:103; documentation/performance/fifo_backtest_2026-09-04.md:3; setup.py:53; tools/check_native_warnings.py:49.
- Principle: measured latency/capacity, controlled toolchains, explicit release scope.
- Evidence: a substantial brief/budget already exists, but representative strategy/load, resource/cancellation envelope, and final acceptance decisions remain open. Recorded three-year FIFO execution is 9.084–9.150 seconds versus the proposed <=5-second target, and approximately 59 seconds for its complete harness. The corpus is synthetic; three trials do not establish a production p95. Native warnings and sanitizers exist, but no dedicated static analysis gate was found.
- Consequence: current evidence supports an experimental characterization engine, not a general production or five-second FIFO claim. Existing documentation correctly states this limitation.
- Change: complete the existing contract with workload and host ownership, resource/overload bounds, cold-load and full-path distributions, reference/parity evidence, and rollback criteria before promotion; add proportionate native static analysis. Profile the FIFO path if the five-second objective remains required.
- Acceptance: approved scope with representative replay, controlled repeated measurements, explicit pass/fail objectives, and correctness preserved across optimization. Do not force C++20/CMake migration: the existing ADR explicitly retains C++11, and the standard permits equivalent build contracts. Scope: medium–large, scoped to experimental promotion.

### 10. [Medium] Support, threat, and release documents need reconciliation

- Location: SECURITY.md:3; RELEASE_READINESS.md:3; RELEASING.md:24; CONTRIBUTING.md:7, :47, :58; dist.sh:7.
- Principle: accurate operator guidance and evidence-linked release decisions.
- Evidence: SECURITY.md lacks a supported-version policy and a concrete private-reporting route; it still references the historical hardening plan. No completed threat-model record was found. Release readiness cites an August 31 hosted run predating the September 4 native commits. Contributor setup bypasses frozen installs and understates mypy coverage. dist.sh recommends local Twine upload despite the documented CI-only release policy. The release checklist still uses the older environment-based pip audit command.
- Consequence: maintainers receive conflicting setup/publishing instructions and stale readiness evidence. Hosted checks or publisher/environment settings may be correct, but this checkout does not prove their current state.
- Change: update the release checklist for the reviewed SHA, supported/experimental feature matrix, unresolved items with owners/dates, current audit/check commands, and the private security route. Complete a proportionate threat model for hostile files, writable cache roots/leases, result persistence, and release credentials. Add ownership/PR guidance equivalent to the template's CODEOWNERS and PR template where useful.
- Acceptance: one consistent setup/release procedure and current evidence links; owner confirms support/reporting details and outstanding external checks. Scope: small–medium.

### 11. [Low] Library versus operational-CLI packaging needs an explicit decision

- Location: pyproject.toml console entry point; documentation/operations/factor_cache_maintenance.md; REPRODUCIBILITY.md.
- Principle: apply the template proportionally.
- Evidence: Gambit is primarily a library but ships a cache-management CLI; no container/build-context/runtime-export files were found. The standard permits a library exception and requires containers for independently deployed operational CLIs.
- Change: record whether the CLI is an in-environment library utility or a separately deployed operational artifact. For the former, document the library exception; for the latter, provide a pinned non-root image and smoke test that preserve required filesystem/lease semantics.
- Acceptance: explicit deployment scope with the applicable reproducibility evidence. Absence of a Dockerfile alone is not a release blocker for library usage. Scope: small decision; medium if independently deployed.

### 12. [Low] Architecture import checks miss equivalent dependency syntax

- Location: tests/test_architecture.py:17.
- Principle: executable dependency rules.
- Evidence: calling the detector on `from .strategy import Strategy` returns an empty set; `from gambit import strategy` returns only gambit, bypassing specific forbidden-module checks. Reproduced using temporary source files.
- Consequence: future prohibited dependency edges can pass the existing architecture checks. No such current production violation is asserted here.
- Change: resolve relative imports and from-package aliases to actual Gambit module names; test equivalent spellings and TYPE_CHECKING imports under an explicit policy.
- Acceptance: equivalent forbidden imports trigger the same architecture assertion without importing runtime code. Scope: small.

## Minimal reproduction for findings 1–2

Run with the existing Gambit environment. This creates only in-process objects.

```python
from types import SimpleNamespace
import numpy as np
from gambit.account import Account
from gambit.pq_types import Contract, ContractGroup
from gambit.strategy_components import PercentOfEquityTradingRule, VWAPEntryRule

Contract.clear_cache()
ContractGroup.clear_cache()
group = ContractGroup.get("review")
contract = Contract.create("A", group, multiplier=50)
timestamps = np.array(["2026-09-04T09:30"], dtype="datetime64[ns]")
price = lambda *args: 100.0
account = Account([group], timestamps, price, None, starting_equity=100000.0)

def call(rule, indicators=None):
    if indicators is None:
        indicators = SimpleNamespace()
    return rule(group, 0, timestamps, indicators, np.array([1]), account, (), SimpleNamespace())

allocation = call(PercentOfEquityTradingRule("review", price, equity_percent=0.1))
print(allocation[0].qty)  # 100; monetary allocation requires 2 contracts

try:
    call(VWAPEntryRule("review", 5, price))
except ValueError as error:
    print(error)  # cannot convert float NaN to integer

Contract.create("B", group, multiplier=50)
vwap = call(VWAPEntryRule("review", 5, price, stop_price_ind="stop"),
            SimpleNamespace(stop=np.array([90.0])))
print([(order.contract.symbol, order.qty) for order in vwap])
# [('B', 500)]; expected both A and B, with multiplier-aware quantity 10 each
```

## Metrics and design observations

50 first-party Python modules contain 15,459 physical lines. Largest: strategy.py 1,273; factor_store.py 1,181; pq_utils.py 927; strategy_components.py 829; evaluator.py 784; pq_types.py 771. These counts locate review targets and do not justify refactoring alone. There are 13 broad Exception/BaseException/bare handlers; sampled strategy handlers preserve context or restore order state and re-raise. No blanket exception-removal recommendation is made.

Measured statement coverage: strategy_components 66%, pq_io 64%, evaluator 69%, portfolio 74%, account 82%, factor_store 86%, strategy 90%, contract_pnl 95%. Prioritize omitted domain behaviors, then appropriate regression floors; raising a headline percentage alone is insufficient. Mypy uses follow_imports=skip and ignore_missing_imports, and the native wrapper exposes little static API information; expand checks selectively when touching those boundaries.

No wholesale layer split, license replacement, forced runtime upgrade, HTTP health endpoints, or exchange integration is warranted by this review. Keep BSD notices and existing C++11 ADR choices. Preserve the explicit deferral of option-pricing validation and the distinction between Strategy and experimental native replay.

## Improvement plan

Proposed owners are roles to assign; no new deadlines or approvals have been assumed.

| Priority | Smallest safe slice | Findings | Acceptance evidence | Proposed owner | Due/trigger | Status |
|---:|---|---|---|---|---|---|
| 1 | Regressions and multiplier-aware entry sizing | 1 | Monetary allocation/stop/cap assertions | Core maintainer | Before production release | Not started |
| 2 | Correct VWAP cardinality/default boundaries | 2 | Empty/single/multiple/default and end-to-end tests | Core maintainer | Before production release | Not started |
| 3 | Bind publication to release-SHA quality evidence | 3 | Deliberate failed check prevents publish | Release maintainer | Before next publish | Not started |
| 4 | Frozen CI and constrained build inputs | 4 | Clean reference matrix and build provenance | Build maintainer | Before next publish | Not started |
| 5 | Complete local tooling/gates and required replay correctness | 5–6 | Clean setup runs checks; PR catches harness defect | Core/build maintainer | Next hardening change | Not started |
| 6 | Dependency inventory, audit scope, token permissions | 7–8 | SBOM/checksums/audits and workflow policy | Release maintainer | Before production release | Not started |
| 7 | Current support/threat/release records and deployment decision | 10–11 | Reviewed records with owners/evidence | Repository owner | Release preparation | Not started |
| 8 | Complete native acceptance and performance work | 9 | Representative correctness/capacity/distributions | Native maintainer | Before experimental promotion | Not started |
| 9 | Close architecture-detector gaps | 12 | Syntax-equivalence regression tests | Core maintainer | Next architecture maintenance | Not started |

Follow-up review should use the correction commit, rerun focused reproductions and the complete gate, and inspect hosted release-SHA checks and environment settings. This report changes documentation only and does not close any finding.

## Implementation follow-up — 2026-09-06

The owner subsequently authorized the highest-value changes. This section
supersedes the original improvement-plan statuses for the work listed below;
the preceding review records the baseline evidence and reproductions.

- Findings 1–2 corrected locally: allocation, bracket-stop, and VWAP sizing use
  contract multipliers; bracket caps use monetary notional; VWAP returns all
  orders, handles empty groups, and uses allocation sizing without a stop.
  Nonfinite/invalid sizing inputs fail explicitly, and non-positive equity
  suppresses entry. An unaffordable contract no longer discards other entries.
  The initial regression suite demonstrated 34 failures before correction.
- Finding 3 implemented, hosted execution pending: publication requires the
  same-commit reusable CI workflow plus artifact verification. Required checks
  include the audit, test matrix, sanitizers, notebooks, and strict docs.
  Installed-wheel/sdist checks now exercise sizing, execution, and accounting;
  cibuildwheel runs the same functional smoke script on every wheel platform.
- Finding 4 partially addressed: reference CI installs use the frozen lock;
  uv is pinned to 0.12.7. Fully constrained isolated build dependencies and
  native system libraries remain open, as documented in RELEASING.md.
- Finding 5 substantially addressed: the local gate includes strict native
  warnings, docs/source drift, notebook cleanliness, and artifact inspection.
  pybind11 is available after frozen setup. A 75% entry/execution component
  coverage floor passes the exact unit-only CI selection. Broader critical
  module coverage policies remain a possible follow-up.
- Findings 6 and 8 corrected locally: deterministic benchmark correctness runs
  in required CI; CI/performance tokens explicitly have read-only contents
  permissions and checkout credentials are not persisted.
- Findings 7 and 10 partially addressed: the audit includes supported optional
  runtime extras, and setup/release/sizing guidance is current. SBOMs, native
  inventory/provenance, security/support/threat records, and hosted release
  evidence remain open. Native promotion criteria, deployment-scope decisions,
  and the architecture-detector improvement (9, 11, 12) are deferred.

Validation of the implementation:

- An isolated Git snapshot passed frozen setup and `UV_OFFLINE=1 make check`:
  816 tests, 84% aggregate Python coverage, six coverage floors, lint, typing,
  native warnings, strict Sphinx, notebook cleanliness, and wheel/sdist
  build/metadata/content inspection. Log: /private/tmp/gambit-hardening-check.log.
- Entry/execution coverage increased from 66% to 79.86% in the full
  suite; unit-only coverage is 76.98%, above its new 75% floor.
- Python 3.10 and a separate frozen Python 3.12 environment each passed all
  563 unit tests and coverage floors. Python 3.12 also passed typing and strict
  documentation. Its unit log is /private/tmp/gambit-hardening-py312-unit.log.
- The built Python 3.10 wheel passed native imports, CLI help, and the new
  functional smoke script in a separate core-only environment outside the
  source checkout. No publishing occurred.
- The expanded `make audit` returned no known vulnerabilities for the audited
  environment. Actionlint 1.7.12 accepted all workflows; delivery-policy
  regressions protect required gate ancestry, frozen installs, sanitizer
  rebuilds, and credential restrictions. Final workflow pin/guard adjustments
  were checked again with actionlint and focused tests after the snapshot run.

These changes are local working-tree changes. Hosted Linux/macOS/Python matrix
results, sanitizer execution, repaired-wheel matrix, TestPyPI, and publisher
environment settings must still be verified before publishing. No blanket
production-readiness claim replaces the remaining acceptance work.
