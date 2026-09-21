# LAT-09 qualification review and disposition

Date: 2026-09-20. **Decision: remain experimental; do not promote.** LAT-09 is
complete as an evidence review and disposition. Statistical performance
qualification and production acceptance have **not** been achieved.

This is the engineering disposition requested by the user. Josh Myers remains
the product/repository decision authority; no independent reviewer signature,
production approval, release tag or deployment is represented by this record.
The approved `gambit-fifo-latency-v1` workload, thresholds and timer boundaries
are unchanged. Missing evidence is not treated as a pass, and no target is
retrospectively relaxed.

## Gate review

| Gate | Evidence reviewed or produced | Disposition |
|---|---|---|
| Candidate identity | Base commit `9c550bdae592e975f12046ebbb8d690889fd9206`, explicit dirty-state record and SHA-256 inventory; executable sources held constant during validation | Source-matched working-tree evidence; not a clean, reviewed release commit |
| Synthetic semantics | Preserved six-case LAT-05 native/reference trace arrays re-compared directly; 3,475,416,000 events and 3,230 historical checkpoints; native/source/reference identities and checkpoint hashes verified | Existing full-volume synthetic parity remains applicable; not a new full-volume oracle replay or production-corpus result |
| Current correctness/build | `make check`: 2,283 tests, lint, typing, coverage/mutation policy, native warnings, documentation, notebook hygiene, wheel/sdist verification | Local pass |
| ASan/UBSan | Fresh isolated factor extension and fused-input helper from current sources; 208 tests passed | Local macOS ARM64 pass; leak detection disabled, 16 MiB ASan quarantine; no Linux leak-check claim |
| TSan | Fresh standalone SPSC probe, 1,800,000 records across capacities and consumption modes | Local macOS ARM64 pass; primitive coverage, not a TSan-instrumented Python wrapper |
| Static analysis | Clang analyzer on mapped column, tick ring and top-of-book/FIFO translation units | No diagnostics; not a formal proof of correctness |
| Installed packages | macOS ARM64 wheels on CPython 3.10.20, 3.11.15 and 3.12.11; sdist install on 3.10.20; release smoke, CLI and 97 native tests per environment | Local install/build pass; distinct runtime/dependency identities retained |
| Supported-platform release matrix | No current-source Linux x86-64 release CI evidence; inspected cached Docker image was ARM64 without compiler/test dependencies | Open; ARM64 environment discovery is not x86-64 validation |
| Primary performance qualification | Fresh controlled development replays plus prior screens; no eligible four-session campaign of 200 trials | Ineligible for the approved statistical gate |
| Probe overhead | LAT-03/08 primary-prefix uncertainty; LAT-08 final saturated handoff diagnostics +13.90% overhead | Detailed probes remain opt-in; full-volume primary overhead review open |
| Production workload/storage | Representative owned corpus/strategy, venue calibration and cold/warm production decode/storage evidence not supplied | Open; synthetic raw-record fixtures do not satisfy this gate |
| Independent release/owner review | No same-commit independent release signoff recorded | Open for promotion; this record retains experimental status |

The existing optimized native binary is unchanged:
`9bb6074a0149a302edd4a1f8ab3063f3ae830b15418342738dcacfde450ffb39`.
The review verified **173 preserved compact artifacts** from earlier work.
Historical trace arrays were compared field by field again; historical checkpoint
streams were hash-verified. These distinctions matter: the review did not silently
relabel old runs as fresh executions of the current runner.

## Validation scope and provenance

Tests and fresh sanitizer builds use the content-identified working tree. The
source inventory records the existing uncommitted changes rather than inventing
a commit that contains them. Later edits are the review documents and plans; no
runtime implementation is changed by LAT-09. A new committed candidate still needs
its own source/build mapping and the required release checks.

The isolated wheel and sdist environments import their installed packages outside
the checkout. All four passed 97 FIFO/top-of-book/ring tests and installed-package
order-sizing/accounting smoke checks. The Python 3.12 offline install initially
lacked a compatible cached PyYAML wheel; the approved dependency download and
successful follow-up are retained. This was an environment-preparation failure,
not a suppressed test failure. Installed dependency versions are recorded; the
3.11/3.12 checks are portability observations, not reference-host timing evidence.
Locally built wheels use this host's native libraries; repaired distributable
wheel and Linux x86-64 release checks remain outstanding.

All native timing runs use the existing optimized release binary, never a sanitizer
binary. Sanitizer/compiler checks and package builds finish before timing starts.
The current development observations have no five-minute quiet-period attestation
or independent-session claim. Missing CPU/thermal observations remain missing in
the raw reports. A few complete replays cannot substitute for four eligible
sessions of 50 trials, regardless of their observed speed.

## Fresh controlled observations

Eight isolated release-binary checks completed **3,478,416,000 records** with
matching reviewed input/result controls and no failed workers. The primary/dense
controls are checked by the runner; the two-year controls were additionally
matched to the preserved LAT-05 independent-trace record. Original generation/loading,
hashing, reconciliation, persistence and the one-million-record disposable warmup
were retained. Exact existing compiler/build provenance was supplied and matched.

| Workload | Trials | Execution range, seconds | Full-harness range, seconds | Maximum process RSS, MiB |
|---|---:|---:|---:|---:|
| Full primary, 946,944,000 records | 3 | 3.982–4.068 | 54.881–55.477 | 213.72 |
| Two-year scaling, 631,584,000 records | 1 | 2.707 | 37.078 | 166.89 |
| One-million dense | 2 | 0.008720–0.009460 | 0.299–0.302 | 119.05 |
| Two-million dense | 2 | 0.020517–0.020840 | 0.601–0.611 | 140.56 |

These observed values satisfy the corresponding hard time/resource limits for
these runs, but **do not establish qualified p95 performance or nonregression**.
The runner reports `timing_gate = "ineligible"`: the four sessions of 50 are
absent, the complete reference-host identity could not be observed, and session
conditions were not supplied. In particular, the sandbox's CPU-name query did not
produce the required reference identity; no value was fabricated. The raw summary's
generic independent-evidence checklist is supplemented by the explicit gate review
above, not interpreted as an automatic production verdict.

No qualifying 200-trial campaign was run. Repeating ineligible observations would
not resolve missing session attestation, independent review, platform evidence or
production corpus requirements. The existing thresholds and sample rules remain
mandatory when those prerequisites are ready.

## Retention, rollback and reopening

Retain the original LAT-06 ring, the experimental LAT-05 FIFO optimization, the
legacy loader default and the opt-in LAT-07 fused generator. Keep LAT-08 detailed
diagnostics disabled for acceptance. No new queue/logger dependency, maturity
change, general-strategy migration or production guarantee is adopted.

The [qualification and rollback runbook](../operations/replay_qualification.md)
identifies preserved baseline binaries/sources, reversible option changes,
isolated rebuild instructions and the exact session/acceptance workflow. Any
correctness, resource, lifecycle or portability regression requires stopping the
affected candidate workload; partial results never become successful output.

To reopen promotion, produce a reviewed committed candidate, supported-platform
release evidence, full-volume probe review, the exact four-session performance
campaign, and the representative production correctness/storage evidence required
by P1.2/Milestone 5. Record the named decision authority's acceptance. Do not
automatically start LAT-10: IPC, codec and shared-snapshot work requires a separate
approved product requirement.

The [compact bundle](qualification_decision_2026-09-20/) contains the decision
matrix, inventories, validation logs, controlled observations, provenance and
artifact hashes. Full local builds/environments remain under
`/Users/jkm0607/Projects/gambit-evidence/lat09-2026-09-20`.
