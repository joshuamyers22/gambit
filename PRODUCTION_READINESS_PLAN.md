# Gambit Production Readiness Plan

## Baseline

- Review date: 2026-09-10
- Gambit baseline: `a76ddc7810440ae97d30f6d811bd487054b39fa4`
- Template baseline: [`production-project-template` at `e132c6e`](https://github.com/joshuamyers22/production-project-template/tree/e132c6e1f1844f1112d2aa69d5ca045422b8a5cd)
- Template material applied: `templates/IMPROVEMENT_PLAN.md`,
  `standards/PRODUCTION_REPOSITORY_STANDARD.md`, the Python data/quant
  archetype, the low-latency C++ archetype, and the release-readiness checklist.
- Scope reviewed: repository structure, Python/Cython/C++ source, tests,
  documentation, dependency/build metadata, and GitHub workflows.
- External capability survey: gs-quant and pysystemtrade source snapshots,
  retrieved on 2026-09-10; commit references, comparisons, and follow-up work
  are recorded in the external repository survey below.

This plan supersedes the embedded improvement table in
[`PRODUCTION_TEMPLATE_REVIEW_2026-09-06.md`](PRODUCTION_TEMPLATE_REVIEW_2026-09-06.md)
for open work. That report and
[`ADVERSARIAL_REVIEW_PLAN.md`](ADVERSARIAL_REVIEW_PLAN.md) remain historical
evidence; completed findings should not be reopened without a new reproduction.

## Target outcome

- **Current evidence:** the frozen lock, lint, mypy, 1,576 tests, 85% aggregate
  coverage, six focused coverage floors, native warning checks, strict Sphinx
  build, notebook cleanliness, and local wheel/sdist inspection pass at the
  Gambit baseline. CI uses read-only permissions, pinned actions, a supported
  Python/OS matrix, native sanitizers, and same-commit release gating.
- **Desired measurable state:** a clean checkout can reproduce every supported
  check and artifact from declared inputs; supported financial behavior passes
  independent acceptance evidence; hostile inputs fail closed; release artifacts
  have complete dependency/native provenance, checksums, and an SBOM; and a
  TestPyPI-to-PyPI release drill succeeds with documented rollback and support.
- **Constraints and behaviors that must not change:** preserve the documented
  public Python API and persisted-result compatibility unless SemVer migration
  steps are followed; preserve causal event ordering, whole-unit quantities,
  multiplier-aware accounting, fees, risk decisions, deterministic replay, and
  failure atomicity. Do not weaken correctness or audit behavior to meet a speed
  target. Keep the native replay API experimental until its separate acceptance
  contract passes. Do not change the BSD-3-Clause license without owner approval.

## Production boundary

The initial production target is a **research and historical-backtesting Python
library** for CPython 3.10-3.12 on the documented Linux and macOS platforms. It
is not a live order-routing or autonomous trading service. The factor-cache CLI
is assumed to be an in-environment maintenance utility unless the owner approves
independent deployment; that decision is an explicit task below.

The package must publish a feature-status matrix before release:

| Capability | Required release posture |
|---|---|
| General `Strategy`, accounting, execution, risk, and result bundles | Supported only after the correctness acceptance suite passes |
| Native CSV/ZIP and HDF5 ingestion | Supported only for the documented limits and trust model after hostile-input gates pass |
| Option pricing, implied volatility, and expiry/settlement | Define and implement supported expiry/settlement behavior and validate numerical results against independent references, or mark experimental/exclude from the stable claim |
| Native factor cache, tick ring, and top-of-book/FIFO replay | Experimental; no general-strategy or production-latency claim |
| Live trading, brokerage connectivity, and production order routing | Out of scope |

The `Development Status :: 5 - Production/Stable` classifier and any equivalent
README wording must not claim more than this matrix. Until all P0 exit criteria
are satisfied, use an accurate pre-production classifier and label the release
candidate accordingly.

## Prioritized improvement plan

P0 items block a production-stable release. P1 items block promotion of the
named capability or broad operational adoption. P2 items are maintainability
work that should be completed during the same hardening cycle when practical.

The external survey adds P1.5–P1.8 and P2.3–P2.7 in its own backlog below.
These are capability improvements, not newly reproduced defects or universal
release blockers. Their adoption triggers identify when they become required.

Functional follow-up: P0.7 and P0.8 record behavioral gaps reproduced at the
baseline. P0.9 and the native-ingestion work in P0.3 record source-inspected
resource/ownership gaps. P1.4 records an acknowledged expiry limitation requiring
characterization before correction. Proposed owners are roles to assign;
existing "In progress" statuses reflect prior hardening unless dated evidence
below says otherwise. "Implemented locally" still requires owner review and
the supported hosted interpreter/platform matrix before release approval.

| Priority | Finding/risk | Smallest safe slice | Acceptance evidence | Proposed owner | Due/trigger | Status |
|---:|---|---|---|---|---|---|
| P0.1 | Product scope and maturity claims are incomplete or inconsistent | Add `PROJECT_BRIEF.md`; publish the supported/experimental/out-of-scope matrix; reconcile README, package classifier, API policy, and release checklist | Owner-approved brief with users, non-goals, failure cost, platforms, data classification, precision/timezone rules, and release criteria; policy test rejects conflicting maturity metadata | Product/repository owner | Before production-stable labeling | Not started |
| P0.2 | Financial correctness is well tested but not yet qualified as a supported product boundary | Build an independent acceptance corpus for accounting, execution, risk, causality, calendars, and persisted results; resolve option-pricing deferral by validation or experimental status | Exact/tolerance rationale, independent expected results, seeded generative cases, and cross-version/platform CI results; all backtests affected by documented corrections are rerun or explicitly invalidated | Quant/domain owner | Before production release | In progress |
| P0.3 | The repository explicitly says hostile-file hardening is incomplete | Add `THREAT_MODEL.md`; complete native parser ownership/resource controls; add coverage-guided malformed CSV/ZIP/HDF5 corpus execution under sanitizers | Threat-model review; enforced compressed/uncompressed, line, row, field, allocation, path, and timeout limits; ASan/UBSan/LeakSan fuzz corpus passes; failures leave no partial or leaked state | Security/native owner | Before supporting untrusted inputs | In progress; native ownership/byte budgets implemented locally 2026-09-11 |
| P0.4 | Build and release inputs are not fully constrained and released artifacts lack a complete inventory | Make the build use a frozen build environment or reviewed constraints; capture compiler, SDK, manylinux image, and `libzip` identity; emit checksums, SBOM, and provenance for the final artifact set | Two clean builds from the same declared inputs succeed; every wheel/sdist has SHA-256, SBOM, source SHA, toolchain/native-library inventory, and CI attestation; policy tests reject unpinned release installers | Build/release owner | Before production release | In progress |
| P0.5 | Hosted release settings and end-to-end publication evidence are not proven by the checkout | Verify protected `main`, required checks, environments/approvals, Trusted Publishers, and Pages; run non-publishing and TestPyPI drills from the release SHA | Links to green same-SHA CI/release runs; nine-wheel matrix plus sdist; clean Linux/macOS installs from TestPyPI; metadata, licenses, attestations, docs, CLI, and rollback/forward-fix checklist signed off | Release owner | Before PyPI/GitHub production release | In progress |
| P0.6 | Security response and repository governance are incomplete | Expand `SECURITY.md`; add PR template, CODEOWNERS/ownership policy, secret scanning, and verified branch/ruleset settings | Supported versions, private reporting route, acknowledgement/remediation targets, and credential-rotation owner are named; deliberate secret fixture fails CI; required reviews/checks prevent an unsafe merge | Repository/security owner | Before accepting external users | Not started |
| P0.7 | Netting opposing pending orders can admit an order that breaches a hard position cap when filled first | Implement worst-case pending-fill exposure checks and define treatment of existing breaches, cancellations, partial fills, and rolls | Cap-100/pending-sell-100/proposed-buy-200 case is rejected; buy/sell fill permutations cannot create a new breach; genuine reductions of existing breaches remain supported | Quant/risk owner | Before production release | Implemented locally 2026-09-11; CI/review pending |
| P0.8 | Historical risk decisions reference mutable order identity | Capture immutable decision-time order identity and terms; use that snapshot for audit reports and persistence | Mutating, filling, cancelling, or reusing the original order cannot alter historical audit fields; persisted snapshots round-trip with explicit format compatibility | Core/risk owner | Before production release | Implemented locally 2026-09-11; CI/review pending |
| P0.9 | Result bundles are read and materialized before resource and shape checks can bound allocation | Add bounded manifest reads, schema validation, and per-table/aggregate resource limits before Arrow materialization | Oversized or malformed bundles fail with bounded payload work and contextual errors; v2/v3/v4 bundles within the supported flat IPC profile still load | Data/storage owner | Before supporting untrusted result bundles | Implemented locally 2026-09-11 for flat IPC; CI/security review pending |
| P1.1 | Data lifecycle, recovery, and reproducibility obligations are spread across feature docs | Define source-of-truth, retention/deletion, schema ownership, migration, cache rebuild, backup/restore, and corrupt/partial-write procedures for result bundles and factor stores | Version migration and empty-to-current tests pass; backup/restore and interrupted-write exercises meet documented RPO/RTO or explicitly state that data is reproducible and disposable | Data/storage owner | Before relying on persisted production research | In progress |
| P1.2 | Experimental native replay has an incomplete acceptance contract and the FIFO path misses the proposed five-second target | Complete `LATENCY_BUDGET.md` from the template; approve a representative strategy, real/preprocessed data, host, capacity, and timer boundary; profile before optimizing | Controlled p50/p95/p99/max and jitter distributions, cold/warm/load/serialization breakdown, memory and saturation results, full reference parity, sanitizer/static-analysis evidence, and an explicit pass/retarget/keep-experimental decision | Native/performance owner | Before native replay promotion | In progress |
| P1.3 | Dependency and security automation do not fully match the current template | Change Dependabot to the `uv` ecosystem, review cadence/groups, add secret scanning and proportionate Python/C++ static analysis, and test workflow policy | Automated lock/action updates produce reviewable PRs; gitleaks and selected SAST/static-analysis jobs are required; workflow-policy tests enforce pins, permissions, timeouts, and credential handling | Build/security owner | Before ongoing production maintenance | Not started |
| P1.4 | Option expiry/settlement timing is unresolved and pricing/IV validation is deferred | Characterize expiry behavior; implement the approved supported settlement model and independently validate pricing/IV, or retain experimental status | Hand-calculated expiry/settlement ledger cases and independent pricing/IV corpus pass, with exact event times and documented tolerances | Quant/accounting owner | Before representing options as production-supported | Not started |
| P2.1 | Legacy duplicate interfaces and source-only test helpers create drift and artifact noise | Remove or delegate `build.sh`/`dist.sh`, retire unused requirements files or generate them from `uv.lock`, consolidate version authority, remove hard-coded developer paths and dormant test functions from `csv_reader.cpp`, and mark historical plans as superseded | `rg` finds no machine-specific source paths; one documented dependency/version/build authority remains; clean artifact contents and `make check` pass | Core/build owner | During hardening cycle | Not started |
| P2.2 | The factor-cache CLI deployment model is undecided | Record it as an in-environment library utility, or add the template's pinned non-root container and smoke test if it is operated independently | ADR states the decision. Library-utility path documents the Docker exception; standalone path builds a digest-pinned image, runs as non-root, and passes CI smoke/restore tests | Product/operations owner | Before operating CLI outside a research environment | Decision required |

## Functional changes and acceptance evidence

### P0.7 — Position limits across possible fill sequences

Evidence: [`RiskContext.projected_position` and `MaxPositionQuantity`](src/gambit/risk.py)
sum signed pending quantities. With zero position, cap 100, pending sell 100,
and proposed buy 200, `decide_order` returned `accepted`. Filling the buy first
would produce a position of 200. This reproduces an admission gap; it does not
assert that every simulator fills those orders in that order.

- [x] Calculate worst-case long and short exposure from independently fillable
  pending orders and the proposal. Do not assume an opposite-side order fills
  first or is guaranteed to fill.
- [x] Specify when genuinely atomic executions may be treated as a group, how
  cancellation acknowledgements release exposure, and what qualifies as reducing
  an existing breach. Document any change from the current net-position policy.
- [x] Add failing-before tests for the reproduced case, reversed signs, partial
  fills, rejection, cancellation requests, mixed instruments, existing breaches,
  and roll legs. Exercise multiple fill permutations through `Strategy` as well
  as standalone risk decisions.

Implemented locally 2026-09-11 in [risk.py](src/gambit/risk.py). The candidate's
same-side reachable endpoint must remain within the cap; an opposite existing
breach can be reduced but cannot justify a new breach across zero. Cancellation
requests still reserve exposure; no atomic fill-group exemption is offered.
[Fill-sequence regressions](tests/test_position_fill_sequences.py) reproduced
13 failing cases before the correction, then passed all 27 cases, including an
exhaustive small partial-fill oracle and six Strategy fill permutations.
The [risk guide](documentation/source/risk_guide.rst) defines compatibility with
the retained net-all-fills helper and separate roll-leg admission.

### P0.8 — Immutable historical risk decisions

Evidence: [`OrderDecision`](src/gambit/risk.py) is a frozen dataclass containing
a mutable `Order`. Reassigning that order's contract changed
`decision.order.contract.symbol` in the reproduction. Freezing the decision
container does not freeze its referenced order or contract.

- [x] Capture detached decision-time identity and terms: contract/group identity,
  submission time, proposed quantity, order type, time-in-force, and relevant
  limit/VWAP/roll terms. Keep immutable policy outcome and reason fields.
- [x] Update audit/report/persistence consumers to use the snapshot. Preserve any
  operational order reference under an explicit compatibility contract; it must
  not be the authority for historical audit values.
- [x] Test later order and contract mutation, partial/final fills, cancellation,
  repeated proposals, and result save/load. Version persisted schema changes and
  avoid fabricating decision-time facts for older records.

Implemented locally 2026-09-11: `OrderDecision.snapshot` is a detached frozen
`OrderSnapshot`; the live `order` reference remains for compatibility only.
Strategy decision tables now use snapshot identity and preserve built-in order
terms in bundle v4. Legacy v2/v3 decision tables retain their available columns,
including on re-save; missing history is not invented. Arbitrary custom
properties/subclass terms are explicitly outside the snapshot contract.
[Snapshot regressions](tests/test_order_decision_snapshot.py) reproduced nine
missing-snapshot failures before implementation; all 12 final cases pass.
[Result tests](tests/test_backtest_result.py) additionally cover mutation before
result reconstruction, persistence, and old-schema round trips.

### P0.9 — Bounded result-bundle loading

Baseline evidence: [`BacktestResult.load`](src/gambit/backtest_result.py) read the
manifest with `read_bytes`, then called `pl.read_ipc(..., memory_map=False)`
before validating table row counts and schemas. Existing hashes detect content
changes but do not impose a resource ceiling on a supplied bundle.

- [x] Bound manifest bytes before reading; validate object structure and metadata
  types before accessing fields. Return consistent `BacktestBundleError` failures.
- [x] Add configurable per-table and aggregate byte/row/column/allocation limits.
  Validate IPC metadata before materialization and reject unsupported encodings
  or compression whose decoded allocation cannot be bounded safely. Do not trust
  manifest declarations alone as an allocation bound.
- [x] Test oversized/malformed manifests, excessive table counts or dimensions,
  encoded-versus-decoded size differences, and checksum-valid oversized inputs
  using small fixtures. Preserve valid v2/v3/v4 compatibility and digest checks.

Implemented locally 2026-09-11 in [result loading](src/gambit/backtest_result.py),
[BundleLoadLimits](src/gambit/bundle_limits.py), and the bounded
[IPC preflight](src/gambit/ipc_validation.py). All nine member files are checked
before the first table decode. Bounded regular-file snapshots prevent growth
past the read budget and path substitution between validation and decoding.
The manifest is not trusted for Arrow dimensions: schemas, actual row/node
counts, batch/body lengths, and buffer/view references are checked independently.

Scope: little-endian, flat, uncompressed primitive IPC columns. Compressed,
nested, dictionary, extension/custom-metadata, and other unsupported layouts are
rejected. Legacy v2/v3/v4 bundles remain readable within that profile and the
configured ceilings; arbitrary custom analytics may require trusted conversion.
No new runtime dependency or bundle-format change was introduced. The
[loading guide](documentation/source/core_concepts.rst) documents defaults and
the distinction between decoded-payload estimates and a hard process RSS limit.
OS isolation, native-decoder review, and hosted qualification remain separate
release obligations; this does not promote hostile-file support globally.

Evidence: the initial [loader regressions](tests/test_bundle_limits.py) failed
36 cases before loader integration. The final loader and
[IPC regressions](tests/test_ipc_validation.py) cover 112 cases, including tiny
forged-length files, compressed inputs, null/string-view allocation accounting,
primitive-type round trips, standard encapsulated schemas, batch limits,
metadata mutations, symlinks/FIFOs, file growth, and checked-byte identity.
Existing result tests preserve old-schema/digest/round-trip behavior.

### P0.3 — Native ingestion ownership and total limits

Baseline evidence: [`csv_reader.cpp`](src/gambit/cpp/io/csv_reader.cpp) and
[`read_file.cpp`](src/gambit/cpp/io/read_file.cpp) retained type-erased owning
vectors and manual ownership transfer. Existing 16 MiB row and 1 GiB ZIP-member
limits and cleanup regressions are already implemented; this is additional
hardening, not a claim that those protections are absent.

- [x] Introduce exception-safe owners for native columns and Python/NumPy
  resources, including partial construction and allocation failure.
- [x] Bound total output allocation and decompression work in the supported
  ingestion entry point; document responsibility for total archive size and
  member-count checks. Keep existing row/member limits and valid CSV semantics.
- [ ] Extend sanitizer/fuzz coverage with allocation-failure, truncated input,
  decompression, and resource-exhaustion cases. Verify complete cleanup on failure.

Local implementation 2026-09-11: `CsvColumn` owns packed bytes; fixed-width
strings no longer retain full source tokens. The NumPy bridge uses scoped Python
owners, NumPy-owned array allocations, and scoped GIL restoration. C++ allocation
failures translate to `MemoryError`. ZIP archive/member/error owners clean up
during constructor failures as well as completed reads. No type-erased owning
column pointers or manual NumPy buffer handoffs remain.

`read_file` now accepts `max_input_bytes` (1 GiB), `max_output_bytes` (256 MiB),
and `max_columns` (4,096). Output budgets cover all selected columns before each
row append; actual input bytes include headers, unselected fields, and read-ahead.
String widths use checked complete parsing. `max_rows` neither eagerly reserves
that capacity nor parses an extra row after reaching its cap. The
[native input guide](documentation/source/native_io.rst) distinguishes logical
output bytes from process RSS and retains caller responsibility for ZIP archive
size/member counts and process time/memory isolation.

Evidence: [native I/O tests](tests/test_native_io_hardening.py) initially reproduced
21 failures against the old extension; all 65 final cases pass. The standalone
[allocation-failure probe](tests/native_csv_allocation_probe.cpp) injects C++
allocation failures until a read succeeds, checks outstanding allocations and
preservation of caller-owned output, and runs CSV/ZIP cases under ASan/UBSan.
The [leak-stress probe](tests/native_memory_probe.py) now exercises budget failures
and missing members as well as successful reads; hosted sanitizer jobs already
include these entry points.

The [draft threat model](THREAT_MODEL.md) now records assets, trust boundaries,
abuse cases, evidence, and unaccepted residual risks using the production-project
template. Named owners/reviewers still need assignment and approval.

The [standalone fuzz target](tests/native_csv_fuzz.cpp) and
[runner](tools/run_native_fuzz.py) add bounded CSV/ZIP libFuzzer campaigns to CI,
with synthetic integer/byte/ZIP seeds and retained failure artifacts. Local
ASan/UBSan seed replay reproduced signed integer overflow in `str_to_int32`;
six new Python CSV/ZIP regressions failed before the fix. Checked unsigned
magnitude parsing now accepts signed extrema and rejects out-of-range i4/i8 and
integer-backed datetime values, preserving in-range prefix/separator behavior.

The third checkbox remains open: local macOS ASan/UBSan and explicit C++ allocation
counts passed, but the installed Apple compiler lacks libFuzzer. Seed replay is
not coverage-guided qualification. Both hosted CSV/ZIP libFuzzer jobs have passed;
the revised independent Linux LeakSanitizer gate still needs hosted verification.
HDF5/IPC coverage-guided targets, longer scheduled
campaigns and named security review remain outstanding. See the
[fuzz guide](tests/NATIVE_FUZZING.md) for exact scope and reproduction instructions.

The separate [NumPy allocator probe](tests/native_numpy_allocator_probe.py) now
injects 425 native array-data failures using a test-only NEP 49 allocator. It
checks cleanup after each of four column allocations, empty/nonempty CSV/ZIP,
datetime parsing/conversion errors, retry success, restored handler identity and
view/handler lifetimes. Negative controls demonstrate live allocations remain
counted until released. Local execution passes without production changes.
The hosted unsuppressed step exposed interpreter-startup allocations in the probe.
A pre-interpreter launcher now excludes startup while tracking each native call;
ARM64 Linux passes all 425 failures with zero leaks and detects a deliberate
16-byte NumPy buffer leak in an independent mandatory control. Hosted x86-64
verification remains pending. Python-object and dtype-descriptor allocation
failure injection remain outside this evidence.

HDF5 follow-up: all selected datasets now pass metadata/aggregate-budget
preflight before any column payload is read. Reader paths and backup groups
resolve only hard links; external/soft links, external raw storage and virtual
datasets are rejected. Row/version values must be integer scalars; manifest
names cannot contain path components/NUL, and variable-length fields inside
compound dtypes are rejected. Unicode outputs count conservatively toward the
existing byte budget. [Regression tests](tests/test_hdf5_hardening.py) reproduced
30 failures before this change and retain legacy/versioned and backup compatibility.
The expanded deterministic HDF5 smoke checks all rejection families per seed;
this is still not coverage-guided HDF5 fuzzing or metadata/process containment.
The internal API migration also removed P2.1's hard-coded native demo routines,
replacing them with an argument-driven smoke program; the other P2.1 work remains.

### P1.4 — Options expiry, settlement, and numerical qualification

Evidence: [`ContractPNL.calc_net_pnl`](src/gambit/contract_pnl.py) flags a case
where a 15:00 expiry order receives a 15:01 trade and records final P&L at the
later time. [`RELEASING.md`](RELEASING.md) separately defers option-pricing and
implied-volatility reference validation. The timing case needs a regression
reproduction before selecting its correction.

- [ ] Define supported cash/physical settlement, exercise/assignment scope,
  settlement-price source, expiry timestamp/timezone/calendar, and interactions
  with execution lag and accounting. Explicitly reject unsupported behavior.
- [ ] Add hand-calculated cases at, before, and after expiry; correct event and
  ledger timing without inventing prices or exposing future settlement data.
- [ ] Validate pricing and implied volatility against independent reference
  cases, including boundary values and numerical tolerances. Keep the feature
  experimental until both lifecycle and numerical acceptance pass.

### P1.2 — Native replay capability and performance

Evidence: the [FIFO benchmark](documentation/performance/fifo_backtest_2026-09-04.md)
records three-year native execution of 9.08–9.15 seconds against the proposed
five-second target. The [replay brief](documentation/architecture/native_tick_backtest.md)
limits the current engine to a fixed strategy and restricted execution models.

- [ ] Identify functionality missing from the approved representative strategy,
  implement those specific capabilities with independent trace parity, and
  reject unsupported order/risk behavior explicitly.
- [ ] Complete Milestone 5's resource and performance work before promotion.
  The general `Strategy` production release does not depend on promoting native
  replay or meeting its proposed latency target.

## External repository survey — gs-quant and pysystemtrade

Surveyed public source snapshots:

- [gs-quant, `f9ab0d283c7fb5b0eca9337338b83841e5716b2d`](https://github.com/goldmansachs/gs-quant/tree/f9ab0d283c7fb5b0eca9337338b83841e5716b2d),
  resolved from `master`.
- [pysystemtrade, `b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8`](https://github.com/pst-group/pysystemtrade/tree/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8),
  resolved from `develop`. The original `robcarver17/pysystemtrade` URL now
  redirects to `pst-group/pysystemtrade`.

Method: inspect relevant implementation files and selected test code, then
compare with Gambit's current modules. Neither upstream suite was installed or
executed. Source observations identify useful patterns; the proposed Gambit
contracts and acceptance criteria are our adaptations, not guarantees that the
upstream projects implement every safeguard described here.

Gambit already has `CalculationContext`, typed `RiskResult` measures, patterned
stress shocks, point-in-time covariance and tail-risk estimates, diagonal
shrinkage, volatility/VaR sizing, FX exposure translation, stage graphs, and a
content-addressed factor DAG. Extend those interfaces where appropriate.

Keep the Polars/NumPy public boundary and offline reference calculations.
gs-quant's [README](https://github.com/goldmansachs/gs-quant/blob/f9ab0d283c7fb5b0eca9337338b83841e5716b2d/README.md)
states that its Goldman Sachs APIs require credentials; those services are not
prerequisites for this roadmap. Implement the selected ideas independently;
do not copy pysystemtrade source into Gambit's BSD distribution. The repositories
identify [Apache-2.0 for gs-quant](https://github.com/goldmansachs/gs-quant/blob/f9ab0d283c7fb5b0eca9337338b83841e5716b2d/LICENSE)
and [GPL-3.0 for pysystemtrade](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/LICENSE).
Any future code reuse requires its own license/notice review under P0.4.

### Survey backlog and delivery order

All rows are planned work. Proposed owners must be assigned before execution;
due triggers are not calendar commitments.

| ID | Improvement | Depends on | Proposed owner | Due/trigger | Status |
|---|---|---|---|---|---|
| P1.5 | Enforced point-in-time data access and revision identity | P0.2, P0.3 | Data/core owner | Before claiming causal access to revised or externally published data | Not started |
| P1.6 | Walk-forward fitting and out-of-sample experiment evaluation | P1.5, existing `Optimizer` | Quant/research owner | Before treating optimized research as validated out of sample | Not started |
| P1.7 | Whole-contract target construction, buffering, and risk rechecks | P0.7, P0.8, existing sizing/FX/covariance APIs | Quant/execution owner | Before executing portfolio-level risk targets through a supported adapter | Not started |
| P1.8 | Futures roll-calendar, raw/adjusted price, and carry pipeline | P1.5, existing roll-order contracts | Futures/data owner | Before supporting continuous-futures research as a built-in workflow | Not started |
| P2.3 | Forecast normalization, caps, and combination | P1.5; P1.6 for estimated weights | Quant/research owner | Multi-rule strategy workflow | Not started |
| P2.4 | Turnover, execution-cost attribution, and sensitivity reports | P0.8, existing costs/accounting; P1.7 for buffering comparisons | Quant/analytics owner | Cost-aware strategy selection | Not started |
| P2.5 | Batched historical/scenario risk with reusable calculations | P0.8, P0.9, P1.5 | Risk/core owner | Repeated portfolio risk across dates and scenarios | Not started |
| P2.6 | Full-revaluation scenarios and sensitivity measures | P1.4, P1.5; P2.5 for batch execution | Quant/pricing owner | Before nonlinear derivative stress is represented as supported | Not started |
| P2.7 | Reusable schedule/risk triggers and composed conditions | P0.7, P0.8, P1.5 | Core/strategy owner | Repeated schedule/condition logic in user strategies | Not started |

Suggested order after the P0 corrections: P1.5, then P1.6 and P1.7; add P1.8
for futures users. Deliver forecast and cost tooling next. Add batch risk,
derivative repricing, and composed triggers as their workflows require them.

### P1.5 — Point-in-time data access and revision identity

Upstream observation: gs-quant's
[DataHandler and Clock](https://github.com/goldmansachs/gs-quant/blob/f9ab0d283c7fb5b0eca9337338b83841e5716b2d/gs_quant/backtests/data_handler.py)
check requested data times against the backtest clock; its
[data-source interfaces](https://github.com/goldmansachs/gs-quant/blob/f9ab0d283c7fb5b0eca9337338b83841e5716b2d/gs_quant/backtests/data_sources.py)
make missing-data behavior explicit.

Gambit gap: [calculation contexts](src/gambit/calculation.py) constrain declared
as-of times, but [strategy callbacks](src/gambit/stages.py) receive full arrays.
Those checks alone cannot prevent a callback from reading future observations
or distinguish an observation time from when a revised value became available.

- [ ] Add an optional owned market-data interface with scalar/window reads
  constrained by the current heartbeat, publication/availability time, and
  dataset revision. Retain explicit source fingerprints in run provenance.
- [ ] Implement fail/missing/stale policies and age-limited forward filling.
  Future-assisted interpolation must not be used in causal execution.
- [ ] Adapt built-in examples and stages to use the interface; document that
  arbitrary callbacks retaining external arrays are outside its enforcement.

Acceptance: changing future observations or later revisions cannot change an
earlier decision; delayed publications remain unavailable until their release;
timezone, same-timestamp, missing, and stale cases pass. Revision changes
invalidate relevant cached factors. This does not claim that gs-quant's clock
alone implements the proposed revision/availability model.

### P1.6 — Walk-forward evaluation

Upstream observation: pysystemtrade's
[fitting dates](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/sysquant/fitting_dates.py)
separate fitting and application periods and support rolling and expanding
windows. Its [optimization test](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/systems/tests/test_mp_optimise_over_time.py)
compares results with and without multiprocessing.

Gambit gap: [Optimizer](src/gambit/optimize.py) schedules suggestions and ranks
costs; the caller currently owns the train/test split and leakage controls.

- [ ] Add a walk-forward runner with explicit fit, validation, and held-out
  intervals, rolling/expanding windows, warm-up policy, and refit schedule.
- [ ] Fit transforms, forecast scalars, covariance estimates, and parameter
  selection only on permitted training data. Support a gap/purge policy when
  labels or holding periods overlap evaluation boundaries.
- [ ] Persist split identities, seeds, selected parameters, failed trials,
  metrics, and model/input hashes with each experiment; produce a chronological
  out-of-sample equity series. Keep in-sample scores visibly separate.

Acceptance: a deliberately predictive future-only feature cannot enter fitting;
perturbing held-out data leaves fitted parameters unchanged; sequential and
parallel runs agree for seeded deterministic inputs; empty/short folds and
boundary overlaps fail clearly. Extend the existing optimizer rather than
introducing another process scheduler.

### P1.7 — Executable targets and turnover control

Upstream observation: pysystemtrade's
[dynamic position optimization](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/systems/provided/dynamic_small_system_optimise/optimisation.py)
combines integer positions, previous holdings, tracking error, trading costs,
position constraints, and a no-trade buffer.

Gambit gap: [volatility/VaR sizing](src/gambit/position_sizing.py) produces
continuous base-currency exposures. It needs a supported bridge to tradable
quantities and incremental orders that accounts for existing and pending positions.

- [ ] Convert exposure targets using current price, contract multiplier, FX,
  and explicit tradable-unit rules. Define zero/negative-price handling and
  reject unsupported conversions instead of silently dividing by zero.
- [ ] Start with deterministic rounding and a configurable no-trade band.
  Add cost-versus-tracking-error optimization only behind that reference path.
- [ ] Recompute risk after rounding; enforce long-only/reduce-only/no-trade and
  position constraints. Account for pending orders so repeated target evaluation
  cannot duplicate outstanding exposure. Submit through existing risk admission.

Acceptance: hand-calculated FX/multiplier cases reconcile to whole-contract
targets; small-capital, impossible-target, partial-fill, pending-cancel, and
existing-breach cases are explicit. Repeating an unchanged target creates no
duplicate proposal; buffering reduces trades on a controlled oscillating fixture
without bypassing required risk reduction. Record achieved risk and tracking error.

### P1.8 — Continuous-futures research and roll data

Upstream observation: pysystemtrade keeps
[priced, forward, and carry contracts](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/sysobjects/multiple_prices.py)
and constructs [adjusted histories](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/sysobjects/adjusted_prices.py)
from contract transitions and price differences.

Gambit gap: [contract universes](src/gambit/universe.py), symbol parsing, and
`RollOrder` exist, but the inspected modules do not provide a corresponding
versioned roll-calendar and continuous-price/carry preparation pipeline.

- [ ] Add validated raw contract-price tables, effective roll schedules, and
  separately identified research adjustments and carry inputs.
- [ ] Preserve tradable raw prices for orders and accounting. Make adjustment
  method, roll differential, missing overlapping prices, expiry, and session
  rules explicit; version historical revisions and their availability.
- [ ] Connect scheduled rolls to the existing roll-order API, including fees
  and admission constraints, without implying atomicity across custom simulators.

Acceptance: a known two-contract roll reconciles orders, costs, cash/P&L, and
research series independently. A roll gap does not become artificial trading
profit; future roll revisions do not alter an earlier causal strategy decision.
Missing overlap data fails or follows a named policy, never an invented price.

### P2.3 — Forecast normalization and combination

Upstream observation: pysystemtrade separates
[forecast scaling/capping](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/systems/forecast_scale_cap.py)
from [weighted combination and diversification scaling](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/systems/forecast_combine.py).

Gambit gap: signals and sizing are extensible, but the inspected code has no
reusable stage for normalizing multiple rule forecasts and combining them.

- [ ] Add Polars stages for scaling, capping, and combining forecasts with
  fixed/equal weights first; persist each rule's contribution and availability.
- [ ] Add historical estimates of weights/correlation and bounded diversification
  scaling only through P1.6. Define missing-rule renormalization, warm-up,
  minimum history, and zero-variance behavior.

Acceptance: fixed-weight fixtures match manual calculations; duplicate correlated
rules do not create unwarranted diversification credit; future data cannot change
earlier weights/scalars; output feeds the existing volatility/VaR sizers.

### P2.4 — Cost and turnover diagnostics

Upstream observation: pysystemtrade's
[account-cost stage](https://github.com/pst-group/pysystemtrade/blob/b4a25e6e1e33a54a3ecfb45c0f6db5e2b60b84f8/systems/accounts/account_costs.py)
estimates turnover and volatility-normalized costs by instrument/rule, including
roll-related holding costs.

Gambit gap: [execution-cost models](src/gambit/execution_costs.py) and accounting
already charge trades. Add diagnostics that explain cost drag and support
comparison of strategy variants, rather than duplicating those charge models.

- [ ] Report gross/net returns, turnover, fees/commission, and separately
  identified slippage/impact estimates by instrument, rule, and period.
- [ ] Capture pre-cost reference prices through immutable execution diagnostics;
  show slippage attribution as a decomposition of realized P&L, not a second fee.
- [ ] Add repeatable cost/participation sensitivity sweeps and buffered versus
  unbuffered comparisons, recording assumptions with each experiment.

Acceptance: costs reconcile to the ledger with no double counting; a zero-cost
fixture has matching gross/net results; a controlled high-turnover strategy shows
its cost drag. Sweeps never assume that higher costs universally lower P&L when
changed fills or risk decisions alter the strategy path.

### P2.5 — Batched historical and scenario risk

Upstream observation: gs-quant's
[PricingContext](https://github.com/goldmansachs/gs-quant/blob/f9ab0d283c7fb5b0eca9337338b83841e5716b2d/gs_quant/markets/core.py)
collects calculations and supports batching, futures, and keyed caching;
[risk results](https://github.com/goldmansachs/gs-quant/blob/f9ab0d283c7fb5b0eca9337338b83841e5716b2d/gs_quant/risk/results.py)
organize measures, scenarios, dates, and portfolio paths for aggregation.

Gambit gap: [calculate_risk](src/gambit/risk_measures.py) loops through measures
for one supplied exposure frame/context. A historical context declares a range,
but that function does not itself iterate dated holdings or build a risk history.

- [ ] Add an explicit bounded request executor over dated holdings, contexts,
  measures, and scenarios, returning the existing long-form Polars result schema.
- [ ] Reuse shared exposure/model work and key cached values by immutable
  holdings, data/model revisions, complete scenario parameters, measure, and
  context. Scenario names alone must not identify a calculation.
- [ ] Define cancellation, partial failures, memory limits, deterministic row
  order, and unavailable results. Begin synchronously; add concurrency only
  after parity and measured benefit, avoiding ambient global pricing state.

Acceptance: batch equals repeated single-date calculations; cache hits/misses
are correct when holdings, FX, models, or same-name shock parameters change;
failures remain visible and no date uses later holdings or observations.

### P2.6 — Nonlinear scenarios and sensitivities

Upstream observation: gs-quant's
[scenario types](https://github.com/goldmansachs/gs-quant/blob/f9ab0d283c7fb5b0eca9337338b83841e5716b2d/gs_quant/risk/scenarios.py)
include market-pattern shocks and volatility-surface slices by expiry/strike.

Gambit gap: [StressScenario.pnl_for](src/gambit/risk_reporting.py) calculates
quantity × multiplier × shocked-price difference. This supports direct price
stress, but does not itself reprice an option after changing underlying spot,
volatility, rates, or time. Patterned shocks already exist and should be reused.

- [ ] Add an owned pricing-model interface and immutable market snapshot for
  the instrument scope validated in P1.4. Implement local reference pricing first.
- [ ] Add spot/volatility/rate/time scenarios, full repricing, and typed
  sensitivities with explicit units, bump sizes, conventions, and model identity.
  Label full-revaluation results separately from linear approximations.

Acceptance: zero shock reproduces the base valuation; sensitivities match
independent finite differences within declared tolerances; large-shock and
near-expiry cases capture nonlinear effects; unsupported instruments fail clearly.
This capability remains conditional on options qualification and does not require
Goldman Sachs services.

### P2.7 — Composable strategy triggers

Upstream observation: gs-quant provides
[periodic, market/risk, aggregate, and negated triggers](https://github.com/goldmansachs/gs-quant/blob/f9ab0d283c7fb5b0eca9337338b83841e5716b2d/gs_quant/backtests/triggers.py),
including all-of and any-of condition combinations.

Gambit gap: [Strategy](src/gambit/strategy.py) and its stage graph accept user
signals/rules, but common timing and condition combinations require custom logic.

- [ ] Add small typed trigger helpers for session-aware schedules, threshold
  crossings, and all/any/not composition, adapting them to existing signals/rules.
- [ ] Define one-shot versus repeated activation, missing-data behavior,
  evaluation order, and decision-time risk snapshots; emit auditable reasons.
  Preserve ordinary callback support and deterministic stage ordering.

Acceptance: helpers match equivalent hand-written rules, behave correctly across
holidays/DST and repeated timestamps, cannot read future risk results, and retain
trade lag, pre-trade controls, and callback rollback contracts.

## Execution details

### Milestone 1 — Truthful scope and correctness gate

- [ ] Create `PROJECT_BRIEF.md` from the Python data/quant template and have the
  owner approve users, supported workflows, non-goals, financial consequence of
  error, data classification, platform matrix, calendar/timezone/units/rounding,
  look-ahead rules, reconciliation source, and recovery objectives.
- [ ] Add one feature-status table shared by README, `API_STABILITY.md`, and
  `RELEASE_READINESS.md`; keep native replay and any unvalidated option APIs
  visibly experimental.
- [ ] Replace the current stable classifier until P0 is complete. Add a policy
  test so metadata, README maturity, and release status cannot contradict one
  another.
- [x] Implement P0.7 position-cap enforcement and P0.8 decision snapshots, then
  include their regressions in the test suite (local evidence 2026-09-11).
- [ ] Run those regressions on the supported hosted matrix and obtain owner
  acceptance before promoting their local evidence to release qualification.
- [ ] If options enter the supported release scope, complete P1.4 lifecycle and
  numerical qualification; otherwise preserve their experimental status.
- [ ] Create compact, reviewable golden cases from an implementation-independent
  oracle. Cover long/short, scale-in/out, cross-zero, partial fills, costs,
  multipliers, rolls, execution lag, VWAP causality, risk rejection, calendar
  boundaries, NaN/Inf, overflow, and persisted-result round trips.
- [ ] Add seeded stateful/property tests for trade/order/account reconciliation
  and run targeted mutation testing on the highest-consequence policy modules.
- [ ] Record which historical outputs must be regenerated after the execution-lag,
  VWAP, sizing, callback, and numeric-admission fixes already in the changelog.

Exit: supported behavior has independent evidence on every supported interpreter
and platform, and unsupported behavior cannot be mistaken for supported behavior.

### Milestone 2 — Security, data, and native boundaries

- [x] Create draft `THREAT_MODEL.md` from the template (named-owner review and
  approval remain open). Include hostile local files,
  ZIP expansion, path/key handling, mapped writable cache roots and leases,
  result-bundle substitution, notebook content, dependency compromise, release
  credentials, denial of service, and accidental sensitive market-data capture.
- [x] Replace type-erased owning `vector<void*>` storage in the native CSV bridge
  with RAII ownership, or document and test an equivalently exception-safe owner.
- [ ] Make parser and persistence limits explicit and configurable where users
  need different safe ceilings. Fail before allocation or mutation when a limit
  is exceeded.
- [x] Implement P0.9 bounded result loading for the documented flat IPC profile
  (local evidence 2026-09-11; hosted/security review remains required).
- [x] Implement the additional P0.3 native ownership and input/output byte
  budgets described above (local evidence 2026-09-11; qualification remains open).
- [ ] Grow the current seeded crash probe into a checked-in malformed-input corpus
  plus coverage-guided fuzz targets. Run bounded corpus regression on every PR
  and longer fuzzing on a schedule under sanitizers.
- [ ] Define bundle/cache schema ownership, compatibility, atomicity, retention,
  deletion, backup, restore, and repair. Exercise interrupted write, corrupt
  metadata, mixed versions, concurrent access, and full-disk/permission failures.
- [ ] Expand `SECURITY.md` with supported versions, a concrete private route,
  acknowledgement/remediation targets, disclosure policy, and response owner.

Exit: the threat model has no unowned high-severity finding, hostile inputs fail
within bounded resources, and recovery procedures have executable evidence.

### Milestone 3 — Reproducible supply chain and CI governance

- [ ] Choose and enforce one frozen build path. Remove unbounded
  `pip install --upgrade build/packaging/twine` operations from release jobs and
  prevent isolated PEP 517 build requirements from drifting outside the reviewed
  dependency set.
- [ ] Pin or record all native inputs: compiler/linker, SDK/deployment target,
  cibuildwheel/manylinux image, architecture, and the resolved `libzip` package
  and libraries copied into repaired wheels.
- [ ] Generate CycloneDX or SPDX SBOMs and a SHA-256 manifest after wheel repair.
  Attach them to the GitHub release with build provenance and attestations.
- [ ] Replace the Dependabot `pip` entry with the template's `uv` ecosystem and
  keep action references at full commit SHAs.
- [ ] Add the template secret-scanning workflow and proportionate static analysis
  for Python and project-owned C++; keep sanitizer and deterministic benchmark
  correctness jobs required.
- [ ] Add `.github/PULL_REQUEST_TEMPLATE.md` and an ownership mechanism. Require
  risk, compatibility, evidence, and rollback notes for financial behavior.
- [ ] Extend `tests/test_delivery_policy.py` to enforce build-input constraints,
  SBOM/checksum generation, security jobs, job timeouts, and release ancestry.

Exit: a clean clone reproduces the release artifact set from declared inputs,
and CI policy prevents bypass of quality, security, and provenance gates.

### Milestone 4 — Hosted release qualification

- [ ] Verify GitHub branch/ruleset protection and required check names for `main`.
- [ ] Verify `testpypi`, `pypi`, and `github-pages` environment protection,
  maintainer approvals, and least-privilege Trusted Publisher records.
- [ ] Run the release workflow without publishing from the candidate SHA; retain
  the complete job/artifact evidence bundle.
- [ ] Publish that same reviewed version to TestPyPI and install platform wheels
  directly, without sdist fallback, in clean Linux and macOS environments.
- [ ] Verify package metadata, license/notices, dependencies/extras, native
  imports, CLI, docs version, hashes, SBOM, provenance, and attestations.
- [ ] Exercise a failed release and the documented recovery path. Because PyPI
  artifacts are immutable, recovery is a new corrective version, not overwrite.
- [ ] Update `RELEASE_READINESS.md` with the exact commit, run links, evidence,
  remaining owned risks, approvers, and decision. Only then restore a
  production/stable maturity classifier and create the signed production tag.

Exit: every P0 row is accepted with same-commit evidence and a named approver.

### Milestone 5 — Experimental native replay decision

- [ ] Copy the template latency-budget structure into `LATENCY_BUDGET.md` and
  link it from the native replay ADR and performance reports.
- [ ] Approve the representative strategy, order/fill rate, real or validated
  preprocessed corpus, execution semantics, hardware, compiler, repetition
  count, memory/audit capacities, overload policy, and measurement boundary.
- [ ] Implement the P1.2 capability gaps required by that approved workload and
  validate their complete order/fill traces before measuring performance.
- [ ] Measure cold load, preprocessing, warm-up, execution, result materialization,
  verification, and peak resources separately and end-to-end.
- [ ] Run full-volume independent trace/accounting parity for the accepted
  workload, not only deterministic native hashes and terminal reconciliation.
- [ ] Add a reproducible optimized profile and native static analysis. Retain the
  existing C++11/setuptools build if it satisfies the equivalent build contract;
  a CMake/C++ standard migration is not required merely to match the template.
- [ ] Profile the FIFO path, change one bottleneck at a time, and preserve
  before/after distributions plus rollback evidence. If the approved target is
  not met, retarget transparently or keep the capability experimental.

Exit: an owner explicitly promotes, retargets, or retains the experimental
status based on production-like correctness and performance evidence.

### Milestone 6 — Research and portfolio workflow improvements

- [ ] Deliver P1.5 causal data access, then P1.6 walk-forward evaluation and
  P1.7 target-to-order construction using the existing data/risk/strategy APIs.
- [ ] Add P1.8 for the futures workflow, with separate research and execution
  price series and auditable roll costs.
- [ ] Demonstrate P2.3 forecast combination and P2.4 cost diagnostics in a
  small end-to-end example from training data through held-out results.
- [ ] Add P2.5 historical risk and P2.7 triggers where needed by that example;
  retain P2.6 as conditional on P1.4 options qualification.
- [ ] Persist experiment, model, target, and diagnostic identities through the
  existing versioned result/provenance system, with migration evidence for any
  newly stored fields.

Exit: each adopted survey row passes its stated acceptance tests and has one
offline reproducible example. The nine-item survey is a roadmap; capabilities
outside the approved release scope remain planned and do not hold up the core
release merely because another library offers them.

## Deferred or explicitly non-applicable items

| Item | Current decision | Reconsideration trigger | Owner |
|---|---|---|---|
| Production service observability (health endpoint, request tracing, on-call service SLO) | Not applicable to the initial in-process library target | Gambit gains a long-running network service or independently operated worker | Product/operations owner |
| Docker image | Documented exception is preferred for an in-environment library utility | The factor-cache CLI is scheduled or deployed independently | Product/operations owner |
| Windows wheels | Outside the currently declared Linux/macOS matrix | User demand plus native dependency/toolchain support and CI ownership | Product/release owner |
| General native replacement for `Strategy` | Explicitly deferred | Representative strategy and parity contract are approved | Quant/native owner |
| Live execution | Out of scope | Separate product brief, threat model, regulatory/risk controls, operational SLOs, and owner approval | Product/risk owner |

## Release acceptance checklist

- [ ] All P0 rows have owners, evidence links, and approval dates.
- [ ] Clean checkout: frozen sync, `make check`, audit, and artifact verification pass.
- [ ] Supported feature corpus passes on CPython 3.10-3.12 on Linux and macOS.
- [ ] Position caps withstand possible pending-order fill sequences, and decision
  audit records remain unchanged after order/contract mutation (P0.7–P0.8).
- [ ] Native ingestion and supported result-bundle loading enforce resource
  limits and clean up partial failures (P0.3/P0.9).
- [ ] Options pass expiry/settlement and pricing/IV qualification if supported;
  native replay passes its separate gate if promoted (P1.4/P1.2).
- [ ] Threat model is approved and no release-blocking security finding is open.
- [ ] Release artifacts contain expected platform wheels/sdist, checksums, SBOM,
  provenance, attestations, licenses, and no source/development contamination.
- [ ] TestPyPI clean-install drill and failure-recovery drill pass.
- [ ] README, API policy, security policy, reproducibility guide, changelog,
  release guide, and maturity metadata describe the same release boundary.
- [ ] GitHub required checks, approvals, environments, and Trusted Publishers are
  verified for the exact release commit.
- [ ] Remaining risks have a named owner, due trigger, and explicit acceptance.
- [ ] `RELEASE_READINESS.md` records approve, approve-with-owned-risks, or block.

## Execution notes

### 2026-09-11 — First implementation slice (P0.7 and P0.8)

- Local full suite: **1,619 passed**, **85% aggregate coverage** on macOS /
  CPython 3.10.20; all six coverage floors passed. Ruff, mypy (53 source files),
  native warnings-as-errors, and Sphinx warnings-as-errors passed.
- Notebook cleanliness passed. Wheel/sdist builds, Twine metadata validation,
  and release-artifact inspection passed separately (`make build` used approved
  network access after sandbox DNS prevented resolving build dependencies).
- `make check` stopped at `tools/check_clean_paths.py documentation/source`
  because the intentionally edited risk guide is uncommitted. The Sphinx build
  itself passed. This is not a claim that the composite gate or hosted CI passed.
- The [changelog](CHANGELOG.md) calls for rerunning results affected by the
  corrected position admission. Old mutable decision identities cannot be
  reliably recovered from terminal orders.
- No commit, push, publication, hosted settings change, or production promotion
  is part of this slice. Other P0 gates remain open. Next implementation slice:
  P0.9 bounded result loading, retaining v2/v3/v4 compatibility.

### 2026-09-11 — Second implementation slice (P0.9)

- Local full suite: **1,731 passed**, **86% aggregate coverage**, macOS /
  CPython 3.10.20. All six coverage floors, lock validation, Ruff, mypy (55 source
  files), native warnings-as-errors, and notebook cleanliness passed.
- Sphinx warnings-as-errors, wheel/sdist builds, Twine checks, and artifact
  inspection passed separately. The combined `make check` is not claimed green:
  its existing clean-docs guard rejects the intentional uncommitted guide edits.
- The first full run caught an optimizable runtime assertion in the new
  preflight. It was replaced with an explicit exception and the full suite rerun.
  The editable rebuild needed approved network access after sandbox DNS failed.
- The parser follows the linked Apache Arrow format specifications. Tests are
  deterministic local preflight checks, not a claim of native coverage-guided
  fuzzing, cross-platform qualification, or total process-memory containment.
- No commit, push, publication, or production promotion. Next implementation
  slice: P0.3 native ingestion ownership and total-allocation controls.

### 2026-09-11 — Third implementation slice (P0.3 native ownership/budgets)

- Local full suite: **1,774 passed**, **86% aggregate coverage**, macOS /
  CPython 3.10.20. All coverage floors, lock validation, Ruff, mypy (55 source
  files), native warnings-as-errors, and notebook cleanliness passed.
- Sphinx warnings-as-errors, wheel/sdist builds, Twine checks, and artifact
  inspection passed. The known clean-docs guard still prevents claiming the
  uncommitted worktree passes the composite `make check` target.
- Standalone CSV/ZIP allocation-failure probes passed with ASan and UBSan.
  LeakSanitizer was not available in that local macOS probe; Linux leak checking
  remains a hosted gate. The extended Python memory-stress probe also passed
  locally without a sanitizer runtime (not leak qualification).
- Rebuilt the native extension through the isolated frozen workflow, using
  approved network access after sandbox DNS prevented resolving build tools.
  No commit, push, publication, or production promotion was performed.
- Next: finish the P0.3 threat model and bounded fuzz/allocator-failure coverage;
  archive-wide work, process limits, and hosted qualification remain explicit gaps.

### 2026-09-11 — Fourth implementation slice (P0.3 threat model/fuzz boundary)

- Added the template-based [draft threat model](THREAT_MODEL.md), including
  explicit unassigned owner/reviewer gates. Updated the security policy and
  corrected stale bundle-version language in the API policy (v4 writes/v2–4 reads).
- Reproduced native signed integer overflow under UBSan and six failing Python
  CSV/ZIP cases, then fixed checked i4/i8/datetime range handling. Valid extrema,
  recovery after rejection and legacy in-range prefixes pass regression tests.
- Added a standalone CSV/ZIP libFuzzer target, bounded runner, synthetic seeds,
  required CI matrix, failure-artifact retention and delivery-policy regression.
  Local Apple Clang lacks the libFuzzer runtime; both formats instead passed
  explicitly labeled ASan/UBSan seed replay. This is not a hosted campaign or
  local LeakSanitizer pass. System libzip remains uninstrumented by this target.
- Local full suite: **1,785 passed**, **86% aggregate coverage**, macOS /
  CPython 3.10.20. Lock, Ruff, mypy (55 source files), all six coverage floors,
  native warnings-as-errors, notebook cleanliness, Sphinx warnings-as-errors,
  wheel/sdist, Twine and artifact inspection passed. Memory-stress probe passed
  without a sanitizer runtime; no leak qualification is inferred from it.
- The existing clean-docs guard still prevents claiming the intentionally dirty
  worktree passes composite `make check`. No commit, push, hosted run, publication
  or security approval occurred. Native editable rebuild used approved network
  access after the sandbox dependency lookup failed.
- Next: test injected NumPy allocator failures; expand HDF5/IPC and scheduled
  coverage-guided campaigns. Hosted sanitizer evidence, archive-wide/process
  containment and named security review remain required before closing P0.3.

### 2026-09-11 — Fifth implementation slice (P0.3 NumPy allocation failures)

- Added a test-only NEP 49 memory handler and isolated Python probe. It forces
  **425 native array-data failures** across four-column plain/ZIP reads, empty
  arrays and datetime conversion; all earlier columns/buffers/policies unwind.
  Retries and retained-view lifetimes pass. Independent allocation controls
  demonstrate that the hook observes live buffers and actually injects failures.
- Existing production ownership code passed without modification. This closes
  the local array-data injection gap, not every Python-object/descriptor or
  dependency allocation path. The probe does not alter the installed extension.
- Added an ASan/UBSan and **unsuppressed** Linux leak-check CI step and a policy
  test protecting it. Broad existing NumPy-stack suppressions are not reused.
  Hosted execution is pending; local `--leak-check` correctly fails when the
  runtime is absent instead of silently presenting ordinary execution as LSan.
- Local full suite: **1,787 passed**, **86% aggregate coverage**, macOS /
  CPython 3.10.20 / NumPy 2.2.6. Lock, Ruff, mypy (55 source files), coverage
  floors, native warnings-as-errors, notebook cleanliness and Sphinx passed.
  The final delivery-policy assertion was also rerun after removing suppressions.
- Wheel/sdist builds, Twine and artifact inspection passed, confirming test
  tooling is excluded. The build used approved network access after sandbox DNS
  blocked dependency resolution.
- No commit, push, hosted run, release or security approval. The existing
  clean-docs guard still prevents claiming composite `make check` passes with
  intentional uncommitted documentation changes.
- Next: broader malformed HDF5/IPC coverage and longer scheduled fuzz campaigns;
  then collect hosted qualification and owner review. Independent financial
  correctness and recovery drills remain separate production gates.

### 2026-09-11 — GitHub push and sixth slice (HDF5 admission)

- At the user's request, committed the first five slices as
  [`ec7390b`](https://github.com/joshuamyers22/gambit/commit/ec7390bbc863f4907e2867c04ff62e448dc5c741)
  and pushed `codex/production-readiness-hardening`. No merge or publication.
- The pushed commit passed all local `make check` stages through documentation
  cleanliness/notebooks. Its build dependency lookup failed under sandbox DNS;
  the build, Twine and artifact checks subsequently passed with approved access.
- [Hosted run 34655051297](https://github.com/joshuamyers22/gambit/actions/runs/34655051297)
  passed both CSV/ZIP coverage-guided fuzz jobs and all 187 native ASan/UBSan
  boundary tests. **Native sanitizer job failed** in the existing memory-stress
  probe: LSan reported one 16-byte allocation from NumPy `default_malloc`.
  The dedicated unsuppressed NumPy allocation leak step was consequently skipped.
  The overall hosted run has completed with a failing conclusion.
  This is unresolved evidence, not a confirmed false positive. No suppression
  was added; investigate it before merge or claiming P0.3 qualification.
- Continued locally with HDF5 admission. Thirty new cases reproduced late
  rejection/unsafe coercion/link/storage/Unicode-budget gaps. The reader now
  completes all-column preflight before payload reads and refuses external
  indirection; fixed-width/legacy/backup/hard-link behavior is preserved.
  Added 38 regression cases and expanded seeded HDF5 rejection smoke coverage.
- Local full suite: **1,825 passed**, **86% aggregate coverage**; Ruff, mypy,
  coverage floors, native warnings, notebook cleanliness, Sphinx, wheel/sdist,
  Twine and artifact inspection passed. These
  follow-up HDF5 changes are not part of the pushed `ec7390b` snapshot.
- Next: investigate the hosted LSan report, then extend IPC/HDF5 coverage-guided
  testing and scheduled campaigns. Archive/file metadata, process isolation and
  security-owner approval remain open. Production promotion is blocked.

### 2026-09-11 — Seventh slice (leak-probe lifetime diagnosis)

- Reproduced the hosted **16-byte NumPy allocation report** in a disposable
  x86-64 Linux container using the pushed `ec7390b` source and NumPy 2.5.2.
  A deeper unsuppressed stack identified `numpy_array` / `read_file_impl` as the
  allocation path. The original stress probe retained its final CSV/ZIP arrays
  at the leak check. Running the workload in its own scope and returning before
  checking removes that specific report; production deallocation code is unchanged.
- Five new probe regressions verify result destruction and checker ordering,
  required-runtime failure, and iteration validation. The ordering regression
  was also exercised against the original probe and fails as expected. CI now
  uses `--require-lsan` and runs the independent NumPy probe after a successful
  build even when the preceding stress probe fails. No suppression was added.
- Local macOS full suite: **1,831 passed**, **86% aggregate coverage**. Lock,
  Ruff, mypy, coverage floors, native warnings and notebook cleanliness passed.
  The x86-64 container passed **79 native I/O/lifetime tests under ASan/UBSan**;
  two separate Clang fuzz-replay tests were deselected because that diagnostic
  image only installed GCC. Leak detection was off for this pytest run.
- Container limitations: Debian/GCC 12/CPython 3.12.11 differs from hosted
  GCC 13/CPython 3.12.14; x86 emulation required the same-version Polars
  compatibility runtime. Neither the lockfile nor the local environment was
  changed for that diagnostic substitution. ARM Linux did not reproduce the
  same 16-byte stack. Both architectures' stripped Python libraries produced
  separate interpreter-retention reports; the independent unsuppressed NumPy
  probe's allocation counters pass but its container LSan report is not clean.
- Evidence logs retained locally in `/private/tmp/gambit-leak-evidence.jE7t12`
  (`x86-original.log`, `x86-scoped.log`, `x86-deep.log`); synthetic test inputs only.
  See [probe documentation](tests/NATIVE_FUZZING.md). These changes and the HDF5
  follow-up remain uncommitted/unpushed; the recorded GitHub run is still failed.
- Next: run the revised checks in the hosted environment and triage remaining
  unsuppressed interpreter allocations. Do not declare all LSan checks clean or
  close P0.3 from removal of a single report. No merge, release or promotion.

### 2026-09-11 — Eighth slice (pre-interpreter leak-check scope)

- At the user's request, pushed the HDF5 and lifetime-probe updates as
  [`aaf5ed0`](https://github.com/joshuamyers22/gambit/commit/aaf5ed078f2fdac2917e07927f159eae0dbea66b).
  [Hosted run 34658154517](https://github.com/joshuamyers22/gambit/actions/runs/34658154517)
  passed every job except native-sanitizers. Its corrected general lifetime probe
  passed; the independent NumPy probe passed all 425 failure counters but reported
  **1,086,365 bytes in 941 interpreter allocations**. No new production leak was
  established by that report.
- Reproduced the old probe's startup-allocation reports on ARM64 Linux
  (1,092,763 bytes / 948 allocations). Python-level `__lsan_disable` occurred after
  initialization. Added a test-only embedded launcher that scopes tracking before
  Python starts, preserves the active venv, and requires balanced enable/disable
  around native calls. No production code, dependency lock or suppression changed.
- The revised Linux probe passed **425 injected failures with LSan result zero**.
  An independent deliberate-leak control through the same native reader produced
  exactly the expected **16-byte NumPy buffer leak**. CI now requires both results
  and retains their logs for seven days. Missing scope/runtime, inactive tracking,
  unrelated leaks and crashes cannot count as successful controls.
- Added 17 regression cases for launcher admission, nesting, error propagation,
  result validation, mandatory controls and diagnostic retention. Full macOS
  suite: **1,848 passed**, **86% coverage**; lock, Ruff, mypy, all coverage floors,
  native warnings and notebook cleanliness passed. The Linux evidence uses
  Debian/GCC 12/CPython 3.12.11/NumPy 2.5.2 on ARM64, not the hosted x86-64 image.
  Sphinx warnings-as-errors also passed. Probe/control logs are preserved at
  `/private/tmp/gambit-lsan-scope-evidence.hVnyS3`; the disposable container was
  removed after copying them. No application data was removed.
- These eighth-slice changes are local/uncommitted. Next: push when requested and
  verify the revised hosted sanitizer gate, then resume HDF5/IPC coverage-guided
  targets and scheduled campaigns. P0.3, owner review and production promotion
  remain open; scoped workload qualification does not establish whole-process
  or dependency leak freedom.

For each slice: add or identify the safety net, reproduce the gap, make the
smallest coherent change, run focused and full gates, attach before/after
evidence, update user-facing contracts, remove obsolete paths, and retain a clear
rollback or forward-fix point. A checked box without linked evidence does not
satisfy a production gate.
