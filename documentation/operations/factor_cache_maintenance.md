# Factor cache operations

The cache CLI is safe-by-default: collection, eviction, and quota commands only
produce a plan unless `--apply` is supplied. Review JSON plans before automating
mutation.

## Observe

```bash
gambit-factor-cache inventory /cache/gambit
gambit-factor-cache health /cache/gambit --minimum-free-bytes 20GiB --max-cache-bytes 500GiB
gambit-factor-cache metrics /cache/gambit --prometheus
gambit-factor-cache metrics /cache/gambit --openmetrics
gambit-factor-cache device /cache/gambit
```

Lifetime counters use a fixed set of names and a process lock. DAG telemetry
remains the source for per-run node identities and timing; the persistent file
contains aggregate counts only. An old lease is a warning, not proof that its
owner is dead. Verify owner liveness before applying garbage collection.

The device command reports cumulative host writes only when Linux sysfs exposes
the backing block device. It does not infer NAND writes, write amplification, or
SSD wear. Compare snapshots around an isolated benchmark to obtain a noisy
whole-device delta; concurrent host I/O remains a confounder.

## Maintain

First inspect a dry-run:

```bash
gambit-factor-cache quota /cache/gambit --max-cache-bytes 500GiB --reserve-free-bytes 20GiB
gambit-factor-cache collect /cache/gambit --stale-lease-seconds 86400
```

After reviewing the output, repeat with `--apply`. For cron, systemd, or launchd,
schedule health and dry-run commands first and alert on findings. Put mutation in
a separate, explicitly enabled job so an observability configuration error cannot
delete cache data. Retain command output for auditability.

Example daily health probe (adjust paths and environment explicitly):

```cron
15 2 * * * /opt/gambit/.venv/bin/gambit-factor-cache health /cache/gambit --minimum-free-bytes 20GiB --max-cache-bytes 500GiB >> /var/log/gambit-cache-health.jsonl 2>&1
```

Do not run collection while an owner may be paused for longer than the configured
lease-age threshold. Metrics recording is best effort after an eviction because a
completed deletion cannot be rolled back if the metrics device becomes full.
