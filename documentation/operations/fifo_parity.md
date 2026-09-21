# Independent FIFO parity runner

`benchmarks/fifo_parity.py` validates the approved synthetic FIFO execution policy
against a standalone implementation. It is correctness work outside all performance
timers. See the [LAT-04 result](../performance/fifo_parity_2026-09-20.md) for the
completed baseline comparison and its limits.

Build with a local GCC/Clang C++11 compiler that supports signed 128-bit arithmetic.
The reference has no third-party or Gambit native dependencies. Run on POSIX
systems; the supervisor uses isolated process groups for bounded cleanup.
No new package dependency or native extension rebuild is required.

```sh
.venv/bin/python benchmarks/fifo_parity.py build \
  --output-dir /tmp/gambit-fifo-reference
.venv/bin/python benchmarks/fifo_parity.py campaign \
  --reference /tmp/gambit-fifo-reference/fifo-reference \
  --output-dir /tmp/gambit-fifo-parity
```

The campaign runs the full 946,944,000-event primary at chunk sizes 65,521, 65,536
and 1,048,576, followed by the 631,584,000-event scaling control and one-/two-million
dense controls. All six use fresh native/reference state. Every output directory
must be new; failures, incomplete runs and prior reports are never overwritten.
Each completed trial prints a status record; inspect `checkpoints.jsonl` for
progress during long runs. The campaign stops on a failed trial.

A single scenario or bounded CI smoke run:

```sh
.venv/bin/python benchmarks/fifo_parity.py run --scenario fifo-smoke-v1 \
  --reference /tmp/gambit-fifo-reference/fifo-reference \
  --output-dir /tmp/gambit-fifo-parity-smoke
```

Check the new reference with sanitizers outside a performance run:

```sh
.venv/bin/python benchmarks/fifo_parity.py build --sanitize \
  --output-dir /tmp/gambit-fifo-reference-sanitized
.venv/bin/python benchmarks/fifo_parity.py run --scenario fifo-2m-dense-v1 \
  --reference /tmp/gambit-fifo-reference-sanitized/fifo-reference \
  --output-dir /tmp/gambit-fifo-parity-sanitized
```

The parent polls combined worker/reference RSS and CPU independently of their
processing loops. The campaign shares at most 24 CPU-hours and additionally limits
wall time to 24 hours, process-group RSS to 4 GiB, and checkpoint silence to 120
seconds. Failure or Ctrl-C terminates the isolated process group, then kills/reaps
it after a one-second grace period. Successful workers also check aggregate CPU
and the conservative sum of worker/reference RSS high-water marks after persisting
traces. Polling and high-water observations are resource enforcement, not a claimed
OS hard memory reservation. Do not extend budgets or convert partial results into
success; retain the failure and review the oracle plan if limits are reached.

Inputs are generated one bounded chunk at a time and discarded after both engines
consume them. Native and reference audits remain bounded by the approved capacity;
no 83 GB input file or full-input residency is needed. Approximate million-event
checkpoints compare every returned audit row and all portfolio scalars, and record
counts and state. A final comparison is mandatory even when the last chunk is
short. State is never reset at a chunk or checkpoint boundary.

| Artifact | Meaning |
|---|---|
| Reference `build.json`, `compiler.log` | Exact command, compiler, source and executable identities |
| `campaign.json`, `summary.json` | Six predeclared cases, shared budgets, completed trials and complete/incomplete decision |
| Trial `request.json`, `trial.json` | Exact configuration and authoritative supervised outcome |
| `checkpoints.jsonl` | Only successfully compared checkpoints, including counts, positions, cash/equity and fees |
| `worker-result.json` | Complete comparison result, identities, resource accounting and artifact digests; insufficient without a successful supervisor trial |
| `native-trace.npz`, `reference-trace.npz` | Every final order, fill, queue, position and scalar from each implementation; load with `allow_pickle=False` |
| `stderr.txt`, `reference-stderr.txt` | Worker/reference diagnostics, including failure details |

The independent wire schema represents each value in an explicit 64-bit word;
Gambit's packed 32-bit ID/status fields are compared by exact value. Binary layout
identity is not assumed. Hashes identify input/output and saved files; the actual
comparison examines every field. First mismatches identify the array, row and
field (or portfolio scalar), and prevent success.

Retain large complete traces and build artifacts outside Git, with immutable file
digests and durable locations in the evidence manifest. Small reports, checkpoint
journals and build logs belong under `documentation/performance`. Re-load the NPZ
files with `allow_pickle=False` and call `fifo_parity.compare_snapshot` to independently
repeat the complete retained-array comparison. Preserve the exact executable and
source identities, not only a branch name or a passing boolean.

The parity report always has `performance_qualification=false`. A completed
synthetic comparison is neither an independent economic-model review nor a
production-corpus/strategy acceptance. Matching native hashes alone are insufficient,
and a prior native binary's parity result is not transferable to an optimized one.

Focused checks:

```sh
.venv/bin/python -m pytest -q tests/test_fifo_parity.py \
  tests/test_fifo_backtest.py tests/test_top_of_book_backtest.py
```
