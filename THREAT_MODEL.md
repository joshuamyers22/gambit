# Threat Model

## Scope and ownership

- System/version: Gambit working-tree production-readiness changes, unreleased,
  source reviewed 2026-09-11. **Draft, not security approval.**
- Owner and reviewers: repository maintainer is the proposed accountable role;
  named security/native, persistence and release reviewers remain unassigned.
  Assign and obtain approval before closing P0.3/P0.6 in the
  [production plan](PRODUCTION_READINESS_PLAN.md).
- Review triggers: next release, new parser/dependency/storage format, changed
  limits/path handling, experimental API promotion, or a security report.
- In scope: local Python backtesting, CSV/ZIP, YAML, HDF5/result bundles,
  experimental mapped factors/rings, notebooks, and delivery.
- Out of scope: live brokerage, multi-tenant hosting, authentication providers,
  execution of untrusted strategies. Simulation risk policies are not access controls.
- Structure follows `templates/THREAT_MODEL.md` from the local
  `production-project-template`, baseline commit
  `e132c6e1f1844f1112d2aa69d5ca045422b8a5cd`.

## Assets, actors, and boundaries

| Asset | Sensitivity | Integrity/availability need | Proposed owner role |
|---|---|---|---|
| Market data, strategy code, configuration | Potentially proprietary/licensed; may accidentally contain secrets | Prevent disclosure; preserve input identity | Data/application owner |
| Orders, fills, account history, result bundles | Financial research/audit records | Correct causality/accounting; no incomplete publication | Core/persistence owner |
| Process, host files, memory and CPU | Inherits invoking user's permissions | Prevent memory corruption, exhaustion and unintended reads | Security/native owner |
| Factor generations and ring leases | Experimental shared local state | No stale buffers or mixed generations | Native/cache owner |
| Source, dependencies, artifacts, release identity | Publication authority is privileged | Qualified code/artifacts; no credential substitution | Release owner |

Actors: data suppliers control file bytes/metadata; local collaborators may modify
shared directories; strategy/notebook authors execute arbitrary Python; contributors
propose code/workflows; maintainers and dependency publishers affect the supply
chain. Arbitrary host code execution is not contained by Gambit.

Entry points, trust boundaries and flows:

1. Caller-chosen files enter `_io.read_file`, `pq_io`, `BacktestResult.load` and
   configuration loaders. libzip, NumPy, h5py/HDF5, Polars/Arrow and YAML are
   external decoding dependencies.
2. Arrays and trusted callbacks enter simulation/accounting state; results and
   provenance return to caller-chosen persistence paths.
3. Experimental factor readers map generation files; publication/leases span
   threads/processes. Read-only mappings do not stop another process truncating
   the underlying file.
4. Repository source and dependencies enter CI/builds, then release publication.
   Notebooks and build scripts are executable code.

Directory ownership, callback trust and process isolation belong to the deploying
application. Checksums are not authentication against someone who can replace
both the data and its manifest.

## Abuse cases and controls

Evidence describes implemented controls, not independent qualification. All
residual risks require a named reviewer; none is accepted by this draft.

| Abuse case | Preconditions | Impact | Prevent/detect/respond controls | Evidence | Residual risk |
|---|---|---|---|---|---|
| T01: CSV memory corruption/integer overflow | Hostile bytes/schema | Crash, corrupted output, possible native compromise | Scoped owners; schema/budget checks; checked i4/i8 magnitude; sanitizer regression; no partial result; tested NumPy data-allocation failures | [Reader](src/gambit/cpp/io/csv_reader.cpp), [tests](tests/test_native_io_hardening.py), [allocator probe](tests/native_numpy_allocator_probe.py) | i1 narrowing and permissive numeric prefixes remain legacy behavior. Python-object/dtype-descriptor allocation failures, hosted unsuppressed leak checking and owner review remain open. |
| T02: ZIP expansion, huge directory or slow input | Hostile archive, special file, repeated calls | Memory/CPU/descriptor exhaustion or blocking | Selected-member declared/actual limits; streamed decompression; RAII cleanup; caller archive-size/count checks and worker isolation required | [Contract](documentation/source/native_io.rst), [fuzz runner](tools/run_native_fuzz.py) | Archive metadata/work, blocking file opens, total RSS/time are not library-bounded. No extraction, but caller paths are not restricted to a safe root. System libzip is not instrumented by this fuzz runner. |
| T03: Bundle substitution/malformed IPC/path races | Untrusted bundle directory | Exhaustion, unintended reads, forged audit trail | Bounded regular-file reads, non-symlink leaf admission, immutable checked bytes, flat IPC preflight, digest/schema validation | [Loader](src/gambit/backtest_result.py), [preflight](src/gambit/ipc_validation.py), [tests](tests/test_bundle_limits.py) | Ancestor directories still require trust. Payload estimates are not RSS caps. Digests are not authentication; native decoder bugs remain possible. |
| T04: HDF5 metadata/links/compression | Hostile HDF5 file | Exhaustion, unintended external file reads | Group/schema/row/column/logical-byte checks; reject variable-length datasets; staged replacement/backup recovery | [Persistence](src/gambit/pq_io.py), [tests](tests/test_hdf5_hardening.py) | File opening/attributes precede payload checks. External links/filters, metadata, decompression and Unicode memory are not fully bounded. Use filesystem-restricted isolated workers. |
| T05: Configuration/notebook/callback injection | Untrusted project directory or executable content | Code execution, path redirection, exfiltration | Safe YAML loading; typed strategy options; trusted-code-only execution; synthetic fixtures and output review | [Utilities](src/gambit/pq_utils.py), [strategy](src/gambit/strategy.py), [notebook check](tools/check_notebook_cleanliness.py) | Safe YAML is not a resource sandbox. Home/local config overlay requires trusted directories. Callbacks/notebooks retain full user permissions; never execute them with release secrets. |
| T06: Cache mutation/lease misuse | Shared writable root or experimental API misuse | Stale data, crash, invalid results | Format/checksum/bounds validation; generation/lease protocols; concurrency tests | [Cache](src/gambit/factor_cache.py), [tests](tests/test_factor_store.py) | Privileged writers can mutate live mappings. Restrict root permissions. Crash/full-disk/concurrent recovery and stable promotion remain open. |
| T07: Replay/duplication/mutable audit identity | Duplicate events, modified orders or replaced artifacts | Incorrect positions, fills, provenance | Fill/account validation; conservative pending-order limits; frozen decision snapshots; persisted version identity | [Risk](src/gambit/risk.py), [snapshot tests](tests/test_order_decision_snapshot.py), [fill tests](tests/test_position_fill_sequences.py) | No live exactly-once guarantee. Historical snapshots cannot be reconstructed; trusted callbacks can mutate state. Independent financial corpus remains required. |
| T08: Supply-chain/dependency failure | Malicious package/action/contributor or credential holder | Compromised build/release | Frozen environment, pinned actions, read-only default tokens, audit, same-commit gates, artifact inspection | [CI](.github/workflows/ci.yml), [release](.github/workflows/release.yml), [tests](tests/test_delivery_policy.py) | Build dependency ranges/system packages remain inputs. Hosted approvals and Trusted Publishers require owner verification; audit is not proof of safety. |
| T09: Credential/data exposure or privileged insider | Sensitive notebooks/logs/corpus or privileged maintainer | Disclosure, malicious publication, deleted evidence | Synthetic fixtures, notebook cleanliness, least-privilege deployment and independent release review required | [Security policy](SECURITY.md), [plan](PRODUCTION_READINESS_PLAN.md) | Private reporting route, response targets, named incident owner and secret/credential review remain open. Hashes do not stop an insider rewriting evidence. |

## Decisions

- Accepted risks with owner and expiry: **none approved here**. Assign owners,
  review dates and evidence before release/hostile-input support. Experimental
  features retain their [API-policy status](API_STABILITY.md).
- Required tests: financial/unit/integration gates; ASan/UBSan/Linux LeakSanitizer;
  allocator-failure cleanup; CSV/ZIP coverage-guided campaigns; extend genuine
  fuzzing to HDF5/IPC and schedule longer campaigns. Seed replay is not
  coverage-guided fuzzing; a CI definition is not a hosted pass. See
  [fuzz instructions](tests/NATIVE_FUZZING.md).
- Monitoring: CI must fail on sanitizer errors/timeouts and preserve synthetic
  reproducers. Application owners must monitor worker resources, reject repeated
  offending inputs, and protect diagnostics. There is no service-monitoring daemon.
- Incident dependencies: privately preserve a minimal synthetic reproducer,
  version/platform and artifact identity; stop using affected inputs/releases;
  coordinate through [SECURITY.md](SECURITY.md). A verified private route remains
  a release blocker. Do not publish sensitive data or run untrusted reproducers
  on a privileged workstation.
- Recovery dependencies: discard failed strategy instances and rerun fixed code
  with validated inputs. HDF5 backups cover the documented interrupted-swap
  contract only. Cache repair, backup/restore and release rollback still require
  P1.1/P0.5 drills; no complete callback rollback or durable backup is claimed.
