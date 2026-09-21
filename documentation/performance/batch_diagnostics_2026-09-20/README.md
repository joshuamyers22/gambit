# LAT-08 evidence

See the [report](../batch_diagnostics_2026-09-20.md) and [runbook](../../operations/batch_diagnostics.md).

- `raw-trials.tar.gz`: 360 successful worker observations across six screens (960 cases), including the earlier and final-source rounds.
- Six summary JSON files: paired results kept separate by source and workload. `handoff-final` and `fifo-final-*` match the final implementation; earlier screens retain their own source snapshot.
- `validation-reproduction-and-earlier-sources.tar.gz`: successful full/focused/document checks, the earlier failed lease test, measurement scripts, source identities and earlier implementation snapshots.
- `final-source-snapshot.tar.gz`: validated implementation, tests, plans and runbooks.
- `decision.json`: diagnostic-only retention decision, scope, counts and native identity.
- `external-evidence-manifest.json`: hashes of all external evidence.
- `manifest.json`: hashes of this bundle and final repository sources.

All input/factor/result controls and queue accounting passed. Every worker used the unchanged native binary. No trials were pooled across the earlier/final source versions or across prefixes. Warmup and reference processing are excluded from measured record counts. No production or full-volume instrumentation qualification is claimed.
