# LAT-07 evidence bundle

See the [report](../input_preparation_2026-09-20.md) and [runbook](../../operations/replay_input.md).

- `measured-trials.tar.gz`: all 606 workers, requests, raw results, stderr and 11 campaign records.
- `*-summary.json`: individual campaign statistics; workloads remain separate.
- `preliminary-trials.tar.gz`: six excluded preliminary workers.
- `build-validation-and-reproduction.tar.gz`: compiler records, corpus manifests, orchestration scripts and validation logs, including unsuccessful early checks and their successful repeats. Top-level replay Python files here are pre-LAT-07 baseline snapshots.
- `source-snapshot.tar.gz`: final measured benchmark implementation, tests, runbook and plans.
- `decision.json`: experimental retention decision and scope.
- `external-evidence-manifest.json`: hashes of full external evidence, including corpus bytes and native libraries omitted from this bundle.
- `manifest.json`: hashes for this bundle and repository source files.

All measured workers succeeded with identical per-workload input/result controls and the unchanged FIFO binary. Source identities were checked against the measured trials when packaging. Baseline and candidate share the same runner; only the selected input mode changes. No cold-cache, real-data decode or production qualification is claimed.
