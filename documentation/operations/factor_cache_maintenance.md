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
gambit-factor-cache migrate /cache/gambit --max-nodes 10 --max-additional-bytes 20GiB
```

Lifetime counters use a fixed set of names and a process lock. DAG telemetry
remains the source for per-run node identities and timing; the persistent file
contains aggregate counts only. An old lease is a warning, not proof that its
owner is dead. Verify owner liveness before applying garbage collection.

The device command reports cumulative host writes only when Linux sysfs exposes
the backing block device. It does not infer NAND writes, write amplification, or
SSD wear. Compare snapshots around an isolated benchmark to obtain a noisy
whole-device delta; concurrent host I/O remains a confounder.

## Migrate legacy generations

Migration is dry-run by default. It inventories indexed v1/v2 nodes, applies node
and temporary-space limits, and reports active leases. Review the plan before
repeating it with `--apply`:

```bash
gambit-factor-cache migrate /cache/gambit \
  --max-nodes 10 --max-additional-bytes 20GiB --reserve-free-bytes 50GiB
gambit-factor-cache migrate /cache/gambit \
  --max-nodes 10 --max-additional-bytes 20GiB --reserve-free-bytes 50GiB \
  --apply --plan-collection
```

Apply creates and reopens a new immutable v3 generation, verifies exact values,
then atomically changes the node pointer. It never rewrites or deletes the old
generation. Existing leases therefore remain valid; run garbage collection later
to reclaim unleased legacy generations. Reruns skip completed v3 nodes, making a
partially completed migration resumable. Use repeated `--node-key` arguments to
restrict the operation to explicitly selected nodes.

Applied batches persist `migration/checkpoint.json` after each attempted node.
The checkpoint reports counts and the last attempted key; immutable v3 node
pointers remain the authoritative resume state. Expected operational failures are
reported per node and the batch continues, while the command exits nonzero when
any failure is present. Lifetime metrics include migrated nodes/bytes, failures,
and concurrent-pointer conflicts. Check `checkpoint_recorded` and
`metrics_recorded` in command output because these post-publication records are
best effort and cannot roll back an already committed pointer.
`--plan-collection` performs only a post-migration garbage-collection dry-run; it
never deletes the legacy generation. Leased sources are excluded from that plan.
Apply collection later as a separate reviewed command.

Before migrating a large cache, reproduce temporary allocation and device-write
costs on the target filesystem:

```bash
.venv/bin/python benchmarks/factor_migration_benchmark.py \
  --rows 1000000 10000000 --columns 3 \
  --cache-directory /cache/gambit-migration-benchmark \
  --output migration-benchmark.json
```

Use a dedicated empty benchmark directory. The script creates and later collects
its legacy generation, but it leaves the final v3 fixture for inspection; remove
the benchmark directory separately after reviewing the result.

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
