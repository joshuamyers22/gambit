# LAT-09 evidence and disposition

See the [review](../qualification_decision_2026-09-20.md) and [runbook](../../operations/replay_qualification.md).

**Remain experimental.** Review complete; statistical performance and production qualification not achieved.

- `decision.json`: explicit disposition, authority/scope, checks, retained defaults and unmet gates.
- `runtime-source-identity.json`: executable/build/test source fingerprint, unchanged through validation; dirty working-tree evidence, not a clean release commit.
- `controlled-checks.tar.gz`: eight fresh supervised observations, with raw requests, worker results, stderr and watchdog reports.
- `primary-disposition.json`: three full primary observations; timing gate ineligible.
- `prior-evidence-audit.json`: 173 compact artifact hashes verified; six preserved trace-array pairs re-compared, with checkpoint hashes and matching native identity. Not a fresh oracle replay.
- `package-*`, `static-analysis.json`, `tsan-result.json`, `linux-probe.json`, `retained-native-build.json`: scoped platform and build records. The Linux probe is ARM64 environment discovery only.
- `validation-and-reproduction.tar.gz`: scripts and logs, including the failed offline Python 3.12 install and successful follow-up, sanitizer records and full check output.
- `source-snapshot.tar.gz`: content-identified runtime/build/test files and final review documents.
- `external-evidence-manifest.json`: selected external artifacts/binaries and package hashes; disposable environments remain outside the repository.
- `manifest.json`: bundle and final document hashes.

No thresholds, maturity classifiers, default ring, loader or runtime implementation changed. No commit, release or deployment was performed.
