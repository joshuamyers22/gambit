# Replay qualification and rollback

The [LAT-09 review](../performance/qualification_decision_2026-09-20.md) records
**remain experimental**. Local correctness and package checks passed; the approved
statistical performance and production gates have not passed. LAT-09 closes the
disposition review, not those outstanding qualification requirements.

## Retained configuration

- Keep the original concurrent ring selected by the user in LAT-06.
- Retain the measured LAT-05 FIFO traversal as experimental. It does not establish
  general Python Strategy parity or exchange-calibrated execution.
- Use the original `legacy` input mode by default. The fused synthetic generator
  remains an explicit benchmark-only option; buffered/mapped readers are comparison
  tools. Remove `--input-library` when returning to `--input-mode legacy`.
- Leave LAT-08 detailed batch diagnostics off for acceptance. Their measured
  overhead is not subtracted from any timing. Diagnostic trials cannot qualify.
- Keep native replay optional and maturity unchanged. No release, deployment,
  production classifier or production performance guarantee follows this review.

## Reopen the performance gate

1. Prepare a reviewed, committed candidate containing the intended changes.
   Retain the exact source tree, lockfile, native/helper binaries and actual
   compiler/link commands. The LAT-09 working-tree inventory is useful provenance,
   but is not a clean release commit or independent reviewer signoff.
2. Complete supported-platform release checks on that candidate, including Linux
   x86-64 and the applicable macOS/Python matrix. Check repaired distributable
   wheels as well as clean installs; a local wheel using host libzip is not proof
   that a wheel works on a clean foreign machine. Run ASan/UBSan, applicable TSan,
   static analysis and tests on the same sources. Retain failures.
3. Establish the approved reference host/runtime and exact native build identity.
   Record AC power, low-power state, background activity and thermal observations.
   Do not substitute a guessed CPU identity or successful thermal reading for an
   unavailable observation. Resolve the primary instrumentation-overhead question
   with full-volume evidence; prefix results do not close it.
4. Execute exactly four qualifying sessions of 50 full primary trials. Each needs
   its own contemporaneous five-minute quiet interval and session evidence. Session
   IDs alone do not establish independence. Review correlation, background events
   and every raw observation before interpreting the confidence criterion.

Example for one session, after completing its conditions and evidence:

```sh
.venv/bin/python benchmarks/controlled_replay.py run \
  --scenario fifo-3y-sparse-v1 --chunk-size 65521 --trials 50 \
  --session-id qualification-session-1 \
  --session-evidence /path/to/session-1.json \
  --build-manifest /path/to/native-build.json \
  --input-mode legacy --output-dir /new/qualification/session-1
```

Use the required session fields documented by `session_evidence()` in the
[runner](../../benchmarks/controlled_replay.py). Repeat in separately established
sessions, preserving the same source/build/input identity, then aggregate:

```sh
.venv/bin/python benchmarks/controlled_replay.py summarize \
  /new/qualification/session-1 /new/qualification/session-2 \
  /new/qualification/session-3 /new/qualification/session-4 \
  --output /new/qualification/aggregate.json
```

The contract allows at most four execution observations above 5 seconds and at
most four harness observations above 75 seconds, with every execution ≤7.5 seconds,
harness ≤90 seconds and process RSS ≤512 MiB. All correctness/resource/lifecycle
requirements still apply. Do not pool exploratory, diagnostic, fused-input,
different-source or failed sessions into an eligible sample, discard slow rows,
or run extra trials selectively to obtain a pass. A timing-gate pass alone is not
the complete qualification decision.

Production promotion additionally requires an approved representative owned
corpus/strategy, calibrated execution assumptions, cold/warm storage and decode
measurements, complete reference parity for the selected behavior and the remaining
P1.2/Milestone 5 gates. Josh Myers retains product/repository decision authority;
record the named review and final acceptance explicitly. LAT-10 is optional and
requires a separate concrete IPC/codec/shared-snapshot requirement.

## Fallback and rollback

For a diagnostics or input-preparation concern, disable the corresponding option
and reproduce using the default legacy runner in a fresh output directory. Never
reuse a failed engine or present a partial factor/portfolio result as success.

For a FIFO correctness, resource or portability regression, stop using the candidate
for that workload. The LAT-05 pre-optimization source and Python wrapper are
preserved at `/Users/jkm0607/Projects/gambit-evidence/lat05-2026-09-20/baseline`
and `baseline-python`. The retained baseline native SHA-256 is
`593537a814f3364f384df5cb1a33df86aa8b8870edd88c020756026111d5b7d2`; the optimized
retained binary is `9bb6074a0149a302edd4a1f8ab3063f3ae830b15418342738dcacfde450ffb39`.
Verify hashes and run correctness checks before using an archived environment.
The slower baseline does not satisfy the proposed five-second target merely
because it is the rollback choice.

Rebuild archived sources into a new isolated output directory with
`benchmarks/build_handoff_variant.py --source /path/to/baseline --output /new/build`.
Rebuild for the target platform/Python ABI; a preserved macOS 3.10 binary is not a
portable substitute. Preserve the candidate and failure evidence. Do not reset
the working tree, overwrite installed libraries in a live process, or reintroduce
the rejected LAT-06 batch-publication ring during rollback.
