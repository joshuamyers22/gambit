# Factor-DAG mapped-cache baseline — 2026-08-29

This benchmark is a decision record, not a general storage claim. It ran locally
on the same Apple Silicon development host as the earlier factor-cache baseline,
using warm page-cache reads, three repetitions, seven non-nullable `float64`
outputs, and a three-node branching Polars DAG. Exact output equality is checked
with NaNs treated as equal.

| Rows | Polars recompute | Native DAG cold publish | Native DAG warm hit | Polars IPC mmap | Warm hit / recompute |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100,000 | 2.29 ms | 18.35 ms | 6.12 ms | 0.35 ms | 0.37× |
| 1,000,000 | 24.18 ms | 163.86 ms | 57.61 ms | 3.66 ms | 0.42× |

Both runs recorded three cold misses followed by three warm hits. The identity
index, lease handling, and lineage invalidation therefore operate as intended, but
whole-DAG mapped reuse is slower than recomputing these inexpensive vectorized
factors. At one million rows it is also much slower than reading one Polars IPC
file containing the complete output.

## Decision

- Keep the identity/index/executor path experimental and correctness-focused.
- Do not route ordinary full-column Polars factor trees through the native cache by
  default.
- Retain Polars IPC mmap as the full-result reuse baseline.
- Evaluate native reuse only for expensive nodes, selective slices, shared
  ancestors reused across many experiments, or tick-derived factors where avoided
  computation materially exceeds lookup and verification cost.
- Add node cost/size telemetry and a cache policy capable of declining low-value
  writes before integrating this executor into production backtests.

## Admission-policy follow-up

The executor now measures each missed node and supports a cost-aware admission
policy. It estimates publication plus future mapped reads against repeated compute
over an explicit expected-use count. With conservative default calibration
(900 MiB/s reads, 400 MiB/s writes, 1 ms fixed read cost, 2 ms fixed write cost,
and a required 1.1× gain), the benchmark's three cheap nodes are all declined.
This avoids persisting a cache that the measurements already show would lose.
At one million rows, the hint-aware cost execution took 27.23 ms versus 24.02 ms
for direct recomputation and 57.79 ms for forced warm cache hits. The remaining miss
and measurement overhead motivated persistent rejection metadata. Declines are now
stored as expiring policy-keyed hints; subsequent runs skip strict opens for those
known-missing nodes while still recomputing and re-evaluating admission.
