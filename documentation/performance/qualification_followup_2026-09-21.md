# LAT-09 promotion follow-up

Date: 2026-09-21. **Disposition: remain experimental; promotion requirements are
not complete.** A clean candidate and supported-platform release evidence are
now available. The full-volume probe experiment is complete, but its ≤1%
execution-overhead target remains inconclusive. The 200-trial qualification
campaign has not started. Production-data validation is deferred per the user.

Joshua Myers is the named independent reviewer. Naming the reviewer is not a
completed review or promotion approval. The v1 thresholds, original concurrent
ring, legacy loader default and experimental maturity remain unchanged.

## Gate status

| Requirement | Result |
|---|---|
| Committed candidate | Clean source candidate `126451bfc3c16b2a2bca2ce810fbdd9f3accad6d`, pushed on `lat09-release-fixes`; independent review pending |
| Supported-platform release validation | Passed: Linux x86-64, macOS x86-64/ARM64, CPython 3.10–3.12; nine repaired wheels, sdist, isolated installs and release verification |
| Correctness and native safety | Fresh local `make check`: 2,283 tests and all checks passed; release CI quality, native, sanitizer/leak, TSan and fuzz jobs passed |
| Full-volume probe experiment | All 30 alternating pairs completed successfully; 56,816,640,000 primary records, matching controls and retained raw reports |
| ≤1% execution probe target | Inconclusive: median +0.718689%, 95% interval +0.406544% to +1.024613%; no rounding or threshold relaxation |
| Four sessions of 50 primary trials | Not started while the probe target remains unresolved; diagnostic trials cannot substitute |
| Production corpus, calibration and storage | Deferred per user; inspected snapshot archive unsuitable for FIFO semantic validation |
| Independent review and promotion decision | Joshua Myers review pending; no promotion approved |

## Candidate and release checks

The initial clean candidate was `9892c75e41c98a2648e1790d200fa3301a42c606`.
The [release candidate](https://github.com/joshuamyers22/gambit/commit/126451bfc3c16b2a2bca2ce810fbdd9f3accad6d)
adds only a diagnostic-test controller correction in `tests/test_replay_profiling.py`.
Benchmark and native sources are identical between those commits. The original
working branch/index and unrelated `GAMBIT_MCP_PROJECT_PLAN.md` were preserved
when creating the isolated candidates.

The [initial release run](https://github.com/joshuamyers22/gambit/actions/runs/35604445710)
exposed two Linux Python 3.11/3.12 diagnostic tests reporting a 512 MiB high-water
breach. Improved assertions retained the worker's actual failure reason. Those
tests launched replay workers from the accumulated coverage process. Linux
preserves resource-usage metrics across exec, which can carry the forking
process's memory footprint into a new worker's high-water report.
See [getrusage(2)](https://www.man7.org/linux/man-pages/man2/getrusage.2.html).
The failure followed by success after controller isolation is consistent with
this inheritance; the original logs do not contain a separate parent-RSS sample.

The tests now launch through a small standalone controller, matching benchmark
CLI usage. They still enforce the unchanged worker memory ceiling, warmup and
correctness checks, and retain controller memory evidence. No failing test was
skipped, limit raised or measured memory subtracted. The corrected focused suite
passed **15 tests** locally; the previously failing Linux jobs passed in CI.

The [completed release workflow](https://github.com/joshuamyers22/gambit/actions/runs/35605565645)
has **26 successful validation jobs and two skipped publication jobs**. This
includes the six OS/Python quality jobs, native correctness and compiler-warning
checks, Linux ASan/UBSan and leak checks, standalone TSan, fuzz campaigns, wheel
repair, package-content/matrix inspection and isolated core/extra/sdist installs.
The downloaded nine wheels and sdist also passed local `--expected-matrix`
artifact inspection. Their individual hashes are retained. No package was
published, release tagged or deployment performed.

The retained reference-host native binary remains
`9bb6074a0149a302edd4a1f8ab3063f3ae830b15418342738dcacfde450ffb39`.
Its existing [six-case full-volume trace evidence](qualification_decision_2026-09-20.md)
remains applicable to that unchanged binary. This follow-up does not claim a
fresh independent full-volume oracle replay or production-trace comparison.

## Full-volume instrumentation result

The predeclared experiment used exactly **30 alternating pairs**, each replaying
946,944,000 records in a fresh worker at the default chunk size. Both modes kept
the compulsory native-call clocks, input/result verification, parent RSS polling
and outer supervision. The full mode additionally enabled the existing stage
probes. Detailed LAT-08 batch histograms remained off. The legacy input path and
actual attested native build were retained; no timing was adjusted for overhead.

All 60 reports identify the clean measured commit `9892c75`, the approved M4
host/runtime and the same native binary. The later test-only correction changes
neither measurement code nor execution behavior. The timing observations remain
diagnostic: there was no qualifying session attestation or independence claim.

| Observation | Timers-only mode | Full probe mode |
|---|---:|---:|
| Trials | 30 | 30 |
| Execution p50 / p95 / maximum, seconds | 4.042 / 4.480 / 4.494 | 4.067 / 4.449 / 4.462 |
| Harness p50 / p95 / maximum, seconds | 55.605 / 62.023 / 62.148 | 55.835 / 60.742 / 61.078 |

Median paired execution overhead is **+0.719%**, with a deterministic paired
bootstrap 95% interval of **+0.407% to +1.025%**. The interval extends above 1%,
so the target remains inconclusive. Harness overhead is +0.430%, with interval
+0.208% to +0.557%. The maximum observed process RSS across both modes was
**235.75 MiB**. Every worker succeeded and input/result controls matched.

Late observations were slower, including the 4.494-second maximum; all are
retained. No cause is asserted from timing alone. No slow rows were discarded,
extra pairs appended, or rounded interval used to obtain a pass. These 60 trials
do not establish qualified p95 performance and are not part of a 200-trial sample.

## Deferred production evidence and review

Under the user's delegation, the selected semantic scope is the native supported
single-owner passive FIFO long/flat policy. It needs ordered quote and individual
trade events, displayed sizes, aggressor direction and documented timestamp
semantics. General Python `Strategy` parity is not implied.

The inspected local SPX archive is documented by its ingestion schema as
five-minute option snapshots, with prices, sizes and volume but no ordered
individual trade/aggressor stream. A concrete August 3, 2026 Parquet path and the
schema source are recorded in `production-corpus-assessment.json`. This was a
documented-schema suitability review, not a measured production-data replay.
Snapshot volume cannot establish intervening FIFO queue depletion. No trades or
receive timestamps were fabricated, no cold-storage result was claimed, and no
licensed market-data file is included in Git. The user deferred this requirement;
it remains an open gate rather than an approved substitution.

The next engineering step is to resolve the execution-probe target, then perform
the fixed four-session campaign on a reviewed, content-matched candidate. Review
every observation and session correlation before interpreting the binomial gate.
Production correctness/calibration/storage validation and Joshua Myers's explicit
review remain necessary for promotion. The [rollback runbook](../operations/replay_qualification.md)
continues to apply; LAT-10 has not been started.

The [compact evidence bundle](qualification_followup_2026-09-21/) retains all
probe trials, failed and successful CI logs, local checks, artifact hashes,
reproduction scripts and the explicit decision. The included qualification and
correlation scripts were prepared but **not executed**. Full local artifacts are
under `/Users/jkm0607/Projects/gambit-evidence/lat09-promotion-2026-09-21`.
