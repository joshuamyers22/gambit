# LAT-04 independent full-volume FIFO trace parity

Date: 2026-09-20. Contract: [`gambit-fifo-latency-v1`](../../LATENCY_BUDGET.md).
Status: full-volume correctness evidence complete for the approved synthetic
baseline. Native replay remains experimental; latency qualification and production
promotion remain open.

## Reference independence and scope

[`fifo_reference.cpp`](../../benchmarks/fifo_reference.cpp) is a standalone
executable built without Gambit headers, pybind11, native execution helpers or
links to the replay extension. It implements the policy specified by the existing
independent Python oracle and [FIFO model](../architecture/fifo_queue_execution.md).
It uses per-instrument observation counts/modulo and an active-order map, and
computes monetary expressions in signed 128-bit intermediates before checking the
64-bit result range. Gambit's implementation uses countdowns, indexed pending
orders and checked 64-bit arithmetic with a different fee expression. Both consume
the same approved input bytes; the reference does not call the native engine or
use native output to determine its execution decisions.

The standalone implementation was checked against both the Python oracle and
existing hand-derived cases. This is independence from the native execution
implementation, not an independently reviewed economic model: the generator,
policy specification, host and compiler remain common inputs. It establishes the
specified synthetic FIFO behavior, not exchange realism or general Strategy parity.

[`fifo_parity.py`](../../benchmarks/fifo_parity.py) streams bounded immutable chunks
to a fresh native engine and a separate reference process. Both preserve portfolio,
orders and queue state for the entire replay. At approximately one-million-event
checkpoints, it directly compares every field of every order, fill and queue audit
row, all positions, processed count, cash, equity, net P&L and accumulated fees.
The terminal checkpoint includes all emitted audit rows. Comparisons use exact
integer equality; hashes are additional identity checks, never a replacement.

"Complete trace" means every row exposed by the result API. Mutable order/queue
audits are compared at checkpoints, including their final state; this does not
claim a per-event log of internal state transitions that the API does not emit.
Small hand-derived cases additionally check arrival/trade ordering and intermediate
queue behavior. Full native/reference result arrays are retained separately as
compressed NPZ files without object/pickle payloads.

## Full-volume results

| Workload | Chunk records | Events | Order / fill rows | Checkpoints | Worker elapsed | RSS upper bound |
|---|---:|---:|---:|---:|---:|---:|
| `fifo-3y-sparse-v1` | 65,521 | 946,944,000 | 88,776 / 595,838 | 851 | 92.859 s | 1.729 GiB |
| `fifo-3y-sparse-v1` | 65,536 | 946,944,000 | 88,776 / 595,838 | 904 | 94.039 s | 1.757 GiB |
| `fifo-3y-sparse-v1` | 1,048,576 | 946,944,000 | 88,776 / 595,838 | 904 | 116.702 s | 1.870 GiB |
| `fifo-2y-sparse-v1` | 65,521 | 631,584,000 | 59,208 / 397,202 | 568 | 58.574 s | 1.270 GiB |
| `fifo-1m-dense-v1` | 65,521 | 1,000,000 | 55,365 / 95,485 | 1 | 0.507 s | 0.168 GiB |
| `fifo-2m-dense-v1` | 65,521 | 2,000,000 | 110,491 / 190,412 | 2 | 0.990 s | 0.284 GiB |

All three primary runs consumed the canonical **83,331,072,000 input bytes** and
matched input SHA-256
`8e03b1e7d62ff6683a055718ef7818cf5188a56bf48084a1df1fb7095369465f`
and native result SHA-256
`5e663f1117533205af50d1297faae98a791b720dff2215386fecb09272c8b2d6`.
Each matched all **88,776 orders**, **595,838 fills**, **88,776 queue audit rows**,
eight positions and every declared portfolio scalar. Final cash/equity was
**9,966,405,721,267**, total fees **33,602,509,933**, and positions were all zero.
Order statuses were 82,864 filled and 5,912 arrival rejections, with no terminal
open orders. Repeating the full reference comparison at prime, power-of-two and
maximum admitted chunk sizes preserved the same complete result.

The predeclared six-run campaign compared **3,475,416,000 events** and **3,230
portfolio checkpoints** with no mismatches or failed runs. It took **365.321 seconds
wall time** and **383.381 CPU-seconds** across workers and references. Maximum
conservative combined RSS high-water sum was **2,007,777,280 bytes (1.870 GiB)**,
within the approved **4 GiB** bound; CPU use was below the shared **24-hour** budget.
The supervisor also enforced a 24-hour wall limit and a 120-second checkpoint
progress limit. Peak sums may exceed simultaneous residency because the worker
and reference peaks need not coincide. These include snapshot/comparison overhead.

These elapsed/CPU measurements include reference execution, protocol copies,
repeated snapshots, comparison, compression and persistence. They are not the
LAT-02 prepared-chunk execution timer or a latency distribution, and do not
qualify the five-second target or the replay-only 512 MiB resource bound.

## Capability and correctness coverage

No additional native capability was needed for synthetic v1, and no native engine
code changed. The supported-policy coverage is:

| Requirement | Evidence |
|---|---|
| Global ordering and shared cash | All full-volume records pass independent sequence/time validation; hand-derived two-instrument cash contention preserves global priority, with no reset or per-instrument partition of the portfolio |
| Conservative FIFO eligibility | Independent Python/native/reference checks cover arrival-event trade exclusion, latency floors, equal timestamps, exact opposing-side limit-price trades, ahead depletion and partial fills |
| Order lifecycle and residuals | Buy/sell queues, cancellation/replacement, rejected arrivals, quote changes, ignored cancellations/additions, dense open orders and terminal marking are compared |
| Integer accounting and risk | Exact cash, fees, quantities, positions and equity; fee boundaries and insufficient cash; deliberately mutated reference fee rounding is detected |
| Chunk/state continuity | Three complete primary replays plus small independently partitioned reference/native inputs; every audit row and checkpoint state matches |
| Input/resource failure | Malformed fields, gaps, stale/reversed time, monetary/valuation overflow, capacity exhaustion, truncated protocol, changed reference binary identity, CPU/wall/progress/RSS termination cannot produce a successful parity artifact |

Unsupported features remain explicitly outside v1: arbitrary Strategy/risk
callbacks, shorts/leverage, funding, impact, hidden liquidity, multiple active own
orders per instrument, venue cancel acknowledgements and exchange calibration.
Representative production strategy/data selection and its capability gaps remain
open under P1.2; this synthetic result does not close those separate requirements.

## Provenance and retained evidence

The native extension was unchanged from LAT-03. Its SHA-256 was
`593537a814f3364f384df5cb1a33df86aa8b8870edd88c020756026111d5b7d2`;
the native FIFO source hash was
`015888396d9f0685c3fe6e0e58d805a2d5105790dd47d31dfedd76111dc2c702`.
Each worker records the checkout revision/dirty state, generator/contract/runner,
Python policy source, reference source/compiler commands/executable identity,
input/output digests and complete-array artifact digests. Build commands for the
new standalone reference are retained. This does not retroactively attest the
original native extension's build commands.

The local host was the M4 workstation used in LAT-01–03. macOS process metadata
and power observations were retained; CPU-brand and thermal observations blocked
inside the sandbox remain unavailable in the individual reports. Correctness does
not depend on a reference-host timing-eligibility flag. No quiet-period or
independent performance-session attestation is implied.

- [Evidence manifest](fifo_parity_2026-09-20/manifest.json): SHA-256 and byte counts
  for all preserved files, including the large complete traces outside Git.
- [Campaign summary](fifo_parity_2026-09-20/campaign/summary.json) and
  [original declaration](fifo_parity_2026-09-20/campaign/campaign.json).
- [Primary supervised trial](fifo_parity_2026-09-20/campaign/01-fifo-3y-sparse-v1-65521/trial.json),
  [complete comparison record](fifo_parity_2026-09-20/campaign/01-fifo-3y-sparse-v1-65521/worker-result.json),
  and [851 checkpoint records](fifo_parity_2026-09-20/campaign/01-fifo-3y-sparse-v1-65521/checkpoints.jsonl.gz).
  Adjacent per-run directories retain the same reports for every other variant.
- [Standalone build provenance](fifo_parity_2026-09-20/reference-build/build.json),
  [sanitized build](fifo_parity_2026-09-20/sanitized-build/build.json), and
  [sanitized 2m result](fifo_parity_2026-09-20/sanitized-dense/worker-result.json).
- [Retained-array audit](fifo_parity_2026-09-20/artifact-audit.json): all saved native
  and reference arrays were reloaded and compared, including exact integer schemas,
  with their file hashes and checkpoint coverage rechecked.
- [Reproduction and artifact runbook](../operations/fifo_parity.md).

The complete local bundle is preserved at
`/Users/jkm0607/Projects/gambit-evidence/lat04-2026-09-20` (approximately 105 MB).
It includes both complete result traces for every run, the original journals,
reference executables, the unchanged native binary and source snapshots. Compact
reports and compressed journals are retained in this repository. No full input
corpus was written. Copy the complete bundle with its manifest when transferring
this evidence to another machine; the local path is not a public download.

After the campaign, the comparator added explicit integer-schema checks to reject
float coercion. All six retained full traces passed these stricter checks; the
artifact audit records that verifier's digest. The exact driver used for the
campaign is retained as `source/fifo_parity.executed.py`, alongside the updated
verifier. Native and reference execution code did not change.

Existing controlled performance reports continue to say that their own worker did
not run an independent oracle. This external evidence applies only to matching
native binary/source, workload and input identities; it must not be copied into a
future optimized build or cause a timing report to self-qualify. Repeat the parity
campaign for changed native execution semantics/builds before accepting a speedup.

## Verification and next step

- **2,193 tests passed** in the full suite; **135 focused checks** passed across
  the new parity runner, FIFO policy and existing top-of-book engine. New coverage
  includes an intentionally incorrect reference fee calculation, which is detected,
  and schema guards against float coercion.
- Ruff, mypy (58 source files), coverage policy, native warnings (including the new
  reference), Sphinx/document and notebook checks passed. All **10/10 existing
  financial mutations** were killed.
- The standalone reference passed a complete **2,000,000-event dense comparison
  under ASan/UBSan**, with no sanitizer diagnostics. This is reference validation;
  it does not replace future changed-native-build sanitizer/portability gates.
- `make check` passed all steps through documentation. Its isolated release build
  hit sandbox DNS restrictions; the approved `make build` retry produced the wheel
  and source distribution, and Twine/artifact inventory checks passed. Retained
  [check log](fifo_parity_2026-09-20/validation/check.log.gz) and
  [build retry log](fifo_parity_2026-09-20/validation/build-retry.log.gz) preserve both outcomes.
- All six saved complete native/reference result pairs were re-compared, including
  exact integer schemas. File hashes, checkpoint monotonicity/completion, evidence
  manifests, documentation links and whitespace checks passed.

Hosted CI, independent reviewer signoff, production-data validation and a new
controlled performance qualification campaign were not run by this slice.

LAT-04 is complete for the approved synthetic baseline. LAT-05 can now test one
profiled FIFO traversal/state-access optimization against this baseline and
reference. Primary instrumentation-overhead uncertainty, independent performance
sessions, native build/portability/sanitizer gates and owner disposition remain
required before an optimization is accepted or native replay is promoted.
