# Gambit project brief

Status: **draft for product/repository-owner approval**. This brief describes
the initial release boundary; it does not itself qualify Gambit for production.
The current capability posture is maintained in [FEATURE_STATUS.md](FEATURE_STATUS.md).

## Outcome

- **Problem and users:** quantitative researchers and strategy developers need
  a transparent, reproducible way to simulate historical strategies while
  retaining visible assumptions about data, order timing, fills, costs, risk,
  accounting, and persisted results.
- **Measurable success:** a clean checkout reproduces the declared quality and
  artifact checks; the supported correctness corpus passes on CPython 3.10-3.12
  on Linux and macOS; result provenance identifies the code, configuration, and
  registered inputs; and supported workflows reconcile orders, trades,
  positions, costs, P&L, and risk decisions against independent expected results.
- **Critical journeys:** build a strategy from timestamped historical inputs;
  run it with explicit execution and risk assumptions; inspect decisions,
  trades, positions, returns, and risk; persist and reload a versioned result;
  and reproduce or deliberately invalidate the research result.
- **Failure cost:** an incorrect result can lead a user to select, size, or reject
  a strategy on false evidence and can cause financial loss if the research is
  used downstream. The initial product does not place orders, but numerical,
  causal, accounting, or provenance errors remain high consequence.

## Scope

- **Initial boundary:** an in-process Python library for research and historical
  backtesting. General strategy, accounting, execution, risk, and result-bundle
  capabilities remain release-candidate functionality until the P0 acceptance
  gates in the production-readiness plan are approved.
- **Explicit non-goals:** live trading, brokerage connectivity, production order
  routing, autonomous trading, a hosted service, managed market-data collection,
  and a general native replacement for the Python strategy engine.
- **Experimental capabilities:** point-in-time market-data access, option
  pricing/IV/expiry behavior, native factor storage and tick transport, and
  native top-of-book/FIFO replay. Their presence in the package does not make
  them production-qualified.
- **Factor-cache CLI:** an in-environment maintenance utility for reconstructible
  research caches, not an independently deployed service. Independent operation
  requires a separate deployment decision and acceptance evidence.

## Runtime and operating constraints

- **Platforms:** CPython 3.10-3.12; manylinux x86-64 and macOS 15+ x86-64/ARM64
  binary wheels. Compatible POSIX source builds require their own C/C++ toolchain
  and `libzip`. Native Windows builds are not supported.
- **Availability:** no service uptime or on-call objective applies. Runs are
  batch research jobs and should fail explicitly rather than publish a partial
  successful result.
- **Capacity:** library operations are bounded by available process resources and
  documented component-specific limits. Gambit does not promise a general data
  size, throughput, latency, or memory envelope for arbitrary callbacks. Native
  replay retains its separate experimental latency and capacity contract.
- **Recovery objectives:** no authoritative server state is owned by Gambit.
  The target RPO is the last externally retained source input/result bundle; the
  target RTO is the time required to restore that input or rerun the research.
  The repository-owned behavior and caller obligations are defined in the
  [data lifecycle and recovery contract](documentation/source/data_lifecycle.rst).
  Its synthetic drills do not replace owner approval or a drill on owner storage.
  Factor caches are disposable and must be rebuildable.
- **Legal boundary:** BSD-3-Clause project license. Users own authorization for
  market data, models, and downstream use. The library does not provide trading,
  investment, regulatory, or compliance approval.

## Data and numerical contract

- **Source of truth:** caller-owned, timestamped source data plus its declared
  schema/revision identity; immutable run configuration and registered input
  fingerprints; and the resulting versioned bundle. A cache is never the source
  of record. Gambit does not certify vendor data as correct. Pre-correction
  outputs follow [HISTORICAL_OUTPUT_DISPOSITION.md](HISTORICAL_OUTPUT_DISPOSITION.md);
  the external owner register is not stored or inferred by Gambit.
- **Classification and retention:** the repository and tests contain public
  source, synthetic fixtures, and generated research examples. Proprietary or
  licensed market data and user results stay outside Git. Their owner determines
  access, retention, deletion, and backup under the
  [data lifecycle contract](documentation/source/data_lifecycle.rst); owner
  approval and an external storage exercise remain open in P1.1.
- **Time:** strategy grids use non-`NaT`, strictly increasing NumPy `datetime64`
  values. Basic array adapters require a timestamp to represent when the modeled
  value is available, not merely its observation label. The experimental
  point-in-time interface instead records observation and availability
  separately. Normalize timezone-aware inputs before the NumPy boundary;
  exchange calendars do not repair localization mistakes.
- **Precision and units:** quantities are signed, finite, nonzero whole
  instrument units. Prices, fees, and commissions are finite real values;
  instrument multipliers convert price movement to account-currency P&L.
  Slippage belongs in fill price while commission and fees remain separate.
  Do not round quantities implicitly. Price rounding occurs only where an
  explicitly configured component documents it.
- **Accounting:** FIFO lots; multiplier-aware realized and unrealized P&L;
  positive costs reduce net P&L; no double-entry cash ledger, leverage, margin,
  foreign-currency cash translation, exercise, or assignment is inferred.
- **Causality:** a decision may use only information available at its modeled
  decision time. Execution lag is counted in heartbeat indices. Same-bar fills
  require separately justified availability. Future-assisted interpolation and
  hidden retained arrays are outside any causal-access claim.
- **Reconciliation:** `Account` trade history and its position/P&L tables are the
  library's canonical derived accounting record. Reconcile them to immutable
  order-decision snapshots, simulated fills, and independent acceptance cases.

## Core invariants

- Preserve causal event ordering and deterministic replay for identical logical
  inputs and declared configuration.
- Admit only valid whole-unit orders and trades; possible pending fill sequences
  must not create a new hard position-limit breach.
- Apply valid fills atomically at the supported boundary; failures must not be
  reported as successful partial publication.
- Retain immutable decision-time audit terms even if operational orders mutate.
- Account for multipliers, fees, commissions, cross-zero trades, partial fills,
  and rolls without fabricating prices or atomic multi-leg execution.
- Version persisted formats explicitly; never invent unavailable historical
  fields while migrating old results.

## Acceptance and ownership

| Requirement | Verification | Owner | Status |
|---|---|---|---|
| Product boundary and maturity are consistent | `tests/test_delivery_policy.py` and document review | Product/repository owner | Draft; approval required |
| Supported financial behavior is independently qualified | P0.2 golden/property corpus on the supported matrix | Quant/domain owner | In progress |
| Release artifacts are reproducible and complete | P0.4 build, inventory, checksum, SBOM, and provenance evidence | Build/release owner | In progress |
| Same-commit hosted release drill succeeds | P0.5 CI, TestPyPI install, metadata, and recovery evidence | Release owner | In progress |
| Persisted research has an approved lifecycle | P1.1 migration, interruption, backup, and restore exercises | Data/storage owner | In progress |

Production-stable labeling requires every P0 row in
[PRODUCTION_READINESS_PLAN.md](PRODUCTION_READINESS_PLAN.md) to have a named
owner, same-commit evidence, and approval. Until then the package classifier and
release documents must remain pre-production.

## Open decisions

| Question | Trigger | Owner | Record |
|---|---|---|---|
| Which option settlement model, if any, is supported? | Before stable option claims | Quant/accounting owner | P1.4 |
| What retention, backup, RPO, and RTO can the library support? | Before relying on persisted production research | Data/storage owner | P1.1 |
| Which independent corpus qualifies core financial behavior? | Before production release | Quant/domain owner | P0.2 |
| Does native replay remain experimental or receive a measured target? | Before native replay promotion | Native/performance owner | P1.2 / Milestone 5 |
| Does the factor-cache CLI become independently operated? | Before scheduling/deploying it outside a research environment | Product/operations owner | P2.2 |
