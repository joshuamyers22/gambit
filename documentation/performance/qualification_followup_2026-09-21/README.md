# LAT-09 promotion follow-up evidence

The [report](../qualification_followup_2026-09-21.md) records **remain experimental**.
Release validation passed; the full-volume ≤1% execution probe target remains
inconclusive. No 200-trial qualification campaign was run. Production-data
validation is deferred and Joshua Myers's independent review is pending.

`full-volume-probe-trials.tar.gz` contains all 60 successful diagnostic trials,
their requests/results, build identities and retained controls. No slow sample was
removed. `probe-summary.json` contains all 30 paired effects and the unchanged
deterministic bootstrap result. These diagnostic trials cannot become qualification.

The release records identify candidate `126451b` and the passing GitHub run.
Wheel/sdist binaries remain outside Git, with SHA-256 digests and download/run
locations in `release-artifacts.json`. The CI archive retains both initial Linux
failure logs and the successful release log. The reproduction archive contains
the test correction, commands, local validation, and unexecuted qualification
orchestration. An unexecuted script is not evidence of completed sessions.

The external manifest covers the specifically enumerated retained files; it does
not claim to inventory every disposable environment or prior experiment. No
licensed market-data file is included. `manifest.json` hashes this compact bundle.
