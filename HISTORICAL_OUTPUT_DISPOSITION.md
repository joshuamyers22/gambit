# Historical output disposition

Status: **operational policy drafted; quant/domain-owner inventory and approval
required**.

This policy covers Gambit backtest bundles, exported tables, research reports,
and downstream decisions produced before the corrections in
`historical_output_corrections.json`. It does not assert that user-owned outputs
outside this repository have been inventoried. No result bundles are tracked in
this checkout, so `HISTORICAL_OUTPUT_REGISTER.csv` intentionally contains only
the required columns until an owner supplies the external inventory.

## Dispositions

- `retain`: the recorded commit contains every applicable correction, or the
  owner can prove from configuration, execution manifest, inputs, and callback
  records that no pre-correction trigger applied.
- `rerun_required`: valid historical inputs may produce different orders,
  fills, quantities, decisions, or P&L under corrected semantics. The old output
  stays archived and must not support a current conclusion; record the new run
  fingerprint when the rerun completes.
- `invalidate`: the old result depended on inputs, callback behavior, or
  arithmetic now rejected by Gambit. Do not patch its tables or reinterpret it
  as a corrected run. Retain only as clearly labeled historical evidence.

When provenance or trigger evidence is missing, rerun from retained source
inputs. If a faithful rerun is impossible, invalidate. Silence is never a
`retain` decision.

## Review procedure

1. Inventory every bundle, export, report, model-selection decision, and
   downstream artifact in the owner-controlled register. Compute a stable
   `output_id` for files outside Gambit rather than copying proprietary data into
   this repository.
2. Record `provenance_git_commit`, package version, run fingerprint, configuration,
   execution manifest, input fingerprints, and callback/model revision evidence.
   Bundle versions lacking execution manifests or immutable decision snapshots
   do not gain reconstructed fields.
3. For each correction, determine whether the result commit contains all
   `correction_commits` (for example with `git merge-base --is-ancestor`). If it
   does not, evaluate the row's trigger. Package version `1.1.0` alone is not a
   cutoff because these corrections are unreleased on development commits with
   the same version.
4. Record trigger status as exactly `affected`, `unaffected`, or `unknown` and
   separate multiple correction IDs with semicolons. Apply the strictest
   applicable disposition. An `invalidate` decision wins
   over `rerun_required`, which wins over `retain`. Record all applicable
   correction IDs and the evidence used.
5. For reruns, use a commit containing all applicable corrections, the same
   source-data fingerprints and explicit configuration, and reviewed callback
   revisions. Reconcile the replacement against the financial acceptance suite;
   never overwrite the old artifact.
6. Refresh or withdraw downstream reports and decisions, then record reviewer,
   UTC review time, replacement fingerprint, and notes. Two artifacts with
   different run fingerprints are distinct runs, not revisions of one result.

## Correction ledger

| ID | Historical trigger | Disposition |
|---|---|---|
| `pending_position_cap` | Position admission relied on opposing pending exposure, cancellation requests, or breach overshoot | Rerun required |
| `heartbeat_execution_lag` | General Strategy used `trade_lag > 1` | Rerun required |
| `multiplier_aware_entry_sizing` | Affected sizing used non-unit multipliers, bracket notional, or no-stop VWAP allocation | Rerun required |
| `vwap_future_observation_causality` | A calendar-day boundary forced VWAP execution before its end | Rerun required |
| `vwap_invalid_terms` | VWAP stop or execution-window terms were outside the current contract | Invalidate |
| `callback_and_fill_integrity` | Callbacks mutated protected state or returned invalid references, chronology, direction, or fill totals | Invalidate |
| `numeric_admission_and_overflow` | Invalid quantities/numerics or non-finite financial arithmetic reached a published result | Invalidate |

The JSON ledger is authoritative for exact commits, triggers, and test evidence.
Changing a correction, disposition, or register field requires quant/domain
review and an acceptance-test update.

## Completion criteria

P0.2 historical disposition is complete only when the quant/domain owner has
confirmed the inventory boundary, every register row has a non-pending
disposition, required reruns have replacement fingerprints, invalidated outputs
and downstream reports are labeled or withdrawn, and the register is approved.
An empty external register is not evidence that no affected outputs exist.
