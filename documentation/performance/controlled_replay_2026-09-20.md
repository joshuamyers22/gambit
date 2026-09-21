# LAT-02 controlled runner implementation and development evidence

Date: 2026-09-20. Contract: `gambit-fifo-latency-v1`.
Status: runner implemented and locally verified; performance qualification pending.

## Delivered behavior

[`benchmarks/controlled_replay.py`](../../benchmarks/controlled_replay.py) adds
isolated fresh worker trials, a separate parent watchdog, observed memory limits,
bounded storage preparation/replay, canonical workload admission, repeated sessions,
alternating paired screening and attributable JSON reports. The original generator
and native execution policy were not changed. The
[runbook](../operations/controlled_replay.md) defines commands, artifact fields,
build/session evidence, cancellation and interpretation.

The runner records runtime/build/input identities and actual condition observations;
unknown facts remain missing. Native build commands require an explicit artifact
record, not an inference from compiler availability. Full-harness measurement ends
after worker exit and includes report persistence/cleanup; execution is the
separate prepared-chunk sum. Large input streams stay bounded by the admitted
chunk limit. No independent-oracle or production qualification is inferred from
checksums and fill-ledger reconciliation.

## Characterization observations

These were development trials with no five-minute quiet-period attestation or
independent session review. Only one observation per row was collected. They
are not a p95 distribution or a controlled before/after optimization comparison.

| Workload | Records | Execution sum | Full harness | Peak process RSS | Output |
|---|---:|---:|---:|---:|---|
| Primary sparse FIFO | 946,944,000 | 9.316615840 s | 60.598896334 s | 217,841,664 bytes (207.75 MiB) | 88,776 orders; 595,838 fills; canonical input/result hashes match |
| Dense lifecycle control | 2,000,000 | 0.023978957 s | 0.563112125 s | 145,686,528 bytes | 110,491 orders; 190,412 fills; ledger reconciled |

The primary observation is above the five-second execution objective. Its harness
and RSS observations are below their respective budgets, but one sample cannot
qualify them. No latency optimization was implemented in LAT-02.

Raw artifacts preserve the worker identity as executed, including intermediate
runner/contract-code digests from this implementation session. Later containment,
comparison and documentation changes were checked with focused tests; the saved
timings are not represented as a new final-code campaign. The checkout revision
was `9c550bdae592e975f12046ebbb8d690889fd9206` plus the recorded dirty state.
The runner could not read the CPU brand inside its sandbox; that field is null
and `reference_host_matches=false`, even though the approved host was independently
identified during LAT-01. Native build-command attestation was absent. Both
summary artifacts correctly mark timing qualification ineligible.

- Primary [worker result](controlled_replay_2026-09-20/primary_worker-result.json),
  [supervisor trial](controlled_replay_2026-09-20/primary_trial.json),
  [campaign summary](controlled_replay_2026-09-20/primary_summary.json).
- Dense [worker result](controlled_replay_2026-09-20/dense_2m_worker-result.json),
  [supervisor trial](controlled_replay_2026-09-20/dense_2m_trial.json),
  [campaign summary](controlled_replay_2026-09-20/dense_2m_summary.json).

The dense two-million-event controls were frozen from this development run:
input `4935e45d31409271c60b4bea27e2f8ac0026de83160caf54e02aede2745ea035`,
result `2f27fc9c11e94c7dca71e819f8148fb4d33732cbf71facf3407e3e22853a1250`.
They are native regression controls, not independent oracle evidence. The saved
first dense report predates their installation in the registry and therefore
truthfully records `canonical_controls_checked=false`.

## Verification

- Full `make check`: **2,119 tests passed**; Ruff, mypy, coverage policy, native
  warnings, documentation and notebook checks passed. All **10/10 financial
  mutations** were killed. Wheel/source build, Twine and artifact inventory checks
  passed. Native build dependencies required network access after sandbox DNS
  resolution failed; the approved rerun succeeded.
- After final supervisor containment changes, **93 focused tests passed** across
  controlled replay, existing benchmark/FIFO, latency-contract and delivery-policy
  tests. Ruff and whitespace checks passed.
- A final CLI paired smoke campaign completed all **30 baseline/candidate pairs**
  (60 isolated workers), using the same environment as both variants to exercise
  alternating order, venv selection, artifact generation and comparison reporting.
  This is a runner smoke test, not an optimization comparison.
- Real isolated synthetic and storage workers produced identical input/output
  controls with different chunk boundaries. Corrupt/truncated storage, invalid
  workload bounds, incomplete/duplicate campaign data, missing build/host evidence,
  changed result controls, setup/native timeouts, cancellation, RSS failure and
  incomplete worker protocols are tested. A terminated worker is reaped and cannot
  contribute a successful sample. Four versus five primary time-limit misses and
  the maximum limit are tested independently of wall-clock speed.
- Shared performance CI now exercises the controlled smoke command and retains
  its report artifacts. Hosted CI has not been run in this session. No noisy
  shared-runner latency threshold was added.

The local verification did not execute a full 200-trial qualification campaign,
prepare/load the full 83.3 GB storage corpus, run the independent full-volume
Python oracle, or establish instrumentation overhead. Those remain explicit
evidence requirements. Allocation counts and PMU counters are unmeasured/null.

## Follow-on diagnosis (2026-09-20)

LAT-03 is now complete for diagnosis; see the [profile and overhead report](fifo_profile_2026-09-20.md).
It retains native FIFO/ring stacks, full-volume Python attribution and paired
probe comparisons. The primary 1% overhead target remains inconclusive. LAT-04
independent full-volume trace parity is next; approved baseline sessions and
representative storage observations remain separate acceptance evidence. The complete execution target,
correctness gates and production boundary remain in
[`LATENCY_BUDGET.md`](../../LATENCY_BUDGET.md).
