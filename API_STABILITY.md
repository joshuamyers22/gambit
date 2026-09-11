# Gambit API stability policy

Gambit follows semantic versioning for the public Python API beginning with the
next published release. The supported root API is exactly the names listed in
`gambit.__all__`; public submodule APIs are documented in the generated API
reference. Other imported names, native implementation symbols, underscored
modules, and undocumented attributes are internal.

## Compatibility

- Patch releases may fix defects and add optional parameters, but do not remove
  or reinterpret supported behavior.
- Minor releases may add APIs. A public API scheduled for removal first emits a
  `DeprecationWarning` for at least one minor release and is listed in the
  changelog with its replacement and earliest removal version.
- Major releases may remove deprecated APIs or deliberately change contracts.
- Financial correctness and security fixes may require an accelerated change.
  Such exceptions must be documented prominently with migration guidance.
- Persisted `BacktestResult` bundles have their own integer format version.
  Unsupported versions fail closed; migration must be explicit rather than
  silently interpreting an older schema.
  Writers now emit version 3; readers explicitly support version 2's existing
  frame schema and legacy provenance as well. Missing historical execution
  manifests remain absent, not reconstructed from current registrations.

## Experimental native APIs

`MappedFloat64Column`, `TickRing`, `TickFactorProcessor`, and the
`gambit.factor_store` generation API remain development prototypes. Native types
in the root namespace are convenient for discovery; this does not make their
storage layout, publication protocol, or concurrency contract stable. Production
promotion requires the correctness, sanitizer, crash-recovery, and performance
gates in `ADVERSARIAL_REVIEW_PLAN.md`.

`gambit.tick_backtest.TopOfBookBacktester`, its market/FIFO execution models and
book/queue record layouts are also experimental, not general Strategy backends.

## Internal scheduling and debugging storage

Simulator-result order membership and engine-applied fill aggregation use
batch-local identity indexes. Trade order, whole-unit validation, fill bounds,
remaining quantities and scoped rollback are unchanged. The two bookkeeping
steps take linear work in eligible orders plus returned fills and temporary
storage proportional to the current batch, without indexing retained history.
Accounting, callbacks and other execution stages have separate costs; this does
not make the complete engine linear or bounded-memory.

`Strategy.orders_iter` and `Strategy.trades_iter` are internal, fixed-length
sparse sequences, not concrete lists. Indexed bucket reads and list-like bucket
mutations remain available; reading an empty bucket does not retain storage.
The outer sequence does not support adding, removing, or replacing timestamp
slots. Explicit outer slices materialize a list of live bucket views, so avoid
full slices of large timelines. Use `Account.trades()` for canonical trade
history; `trades_iter` remains a legacy debugging aid and is not populated by
execution. This storage change does not introduce a bounded-history policy or
skip idle timestamps in the execution loop.

## Execution provenance

`Strategy.capture_execution_provenance()` snapshots current runtime options,
ordered component descriptions and the stage graph. `run()` calls it before
execution. Registration alone does not finalize a snapshot; recapture after edits.
Source hashes and dataclass parameters do not capture arbitrary callback state,
closures, globals, external data or transitive dependencies. The manifest lists
unresolved scope and must not be treated as a complete reproducibility certificate.
Result provenance is detached from later registrations or parameter changes.

Invalid option types and duplicate YAML keys now fail at the configuration
boundary. Before-run changes to the existing runtime lag/log/final-calculation
attributes are validated and captured. Accounting initialization cannot be
changed in place. Detected mid-run option/provenance/registration drift prevents
result publication; this does not roll back all earlier callback/account effects.
Use a fresh strategy after a failed run. Arbitrary custom callback mutation of
its own state is unresolved, not prohibited by this check.

## VWAP pricing causality

Construction, rule admission and built-in VWAP execution now share execution-
window validation: submission/end timestamps must be NumPy datetime scalars,
non-NaT, and end must not precede submission. Rule admission rejects an invalid
window before any new risk decisions. `VWAPMarketSimulator` checks all VWAP
windows before pricing or filling any order in that invocation, including direct
calls and orders changed after admission. Invalid windows can no longer trigger
immediate backup-price fills. Fix invalid terms and rerun affected backtests.
Zero-duration windows, equivalent NumPy timestamp units and valid end-time edits
remain supported. This does not freeze window fields, validate the optional stop
price, impose custom-simulator policy or expand callback rollback guarantees.

`VWAPMarketSimulator` now excludes observations after the fill timestamp, including
when its calendar-day boundary trigger executes before the requested VWAP end.
Affected historical backtests must be rerun: the old calculation could use future
prices/volumes and change P&L. Available observations still use the existing
positive-price/positive-volume filter, submission-time lower bound and requested
end-time/stop rules; an empty observed window uses the existing current-heartbeat
backup or fails when none is configured. Trigger timing and fill quantities are
unchanged. This is not an exchange-session calendar, streaming VWAP optimization,
or a guarantee that user-supplied indicators themselves are free of look-ahead.

## Simple simulator limit validation

`SimpleMarketSimulator` rechecks the current limit price as a finite real number
at the marketability comparison, after price/slippage callbacks and rounding.
NaN/Inf, booleans, strings and other invalid terms now raise instead of bypassing
the limit comparison or depending on NumPy coercion. This applies to direct calls
and post-admission mutations; finite negative/zero limits remain valid. Missing
market prices still defer execution without reaching this check. Invalid limits
abort candidate construction before the simulator applies any batch fills.
Limit terms and arbitrary callback side effects are not rolled back, and custom
simulators retain responsibility for their own execution policy.

## Strategy roll identity

Roll legs expanded through `Strategy` receive matching private
`_gambit_roll_id` metadata derived from the number of previously recorded order
legs/proposals and the command position in the current callback batch. Identical
submission sequences produce identical IDs without memory addresses; separate
rolls, including repeated submissions of one source command, get separate pairs.
Risk-rejected orders still count toward subsequent submission positions.
IDs are scoped to one strategy, not globally unique across accounts or runs.
Source commands remain unchanged. Standalone `RollOrder.legs()` and rule
validation without a strategy prefix retain their existing process-local
identity; do not treat those IDs as stable replay identifiers. User metadata,
callback state and timing telemetry may still differ across runs.

## Callback order ownership

Rule admission rechecks a real `Contract`, a NumPy datetime scalar and actual
`TimeInForce`/`OrderStatus` enums using the construction-time type policy.
Strings, integers and array-wrapped timestamps are not substitutes. A submitted
timestamp must be non-NaT and equal the current strategy heartbeat; equivalent
NumPy datetime units remain supported. Construction may still leave the default
NaT for an unscheduled order. Malformed batches fail before risk evaluation;
assign valid typed fields before returning orders. This does not add historical
replay protection, change standalone risk calls, or freeze shared contract data.

One rule-return batch may contain each order object only once and may not return
an order that was pending when the callback began, even after cancelling it.
Violations reject the whole batch before risk evaluation and restore protected
pending fields. Return a newly constructed order for a new submission; distinct
objects with identical values are supported. Identity checking is local to the
batch and captured pending set, not a persistent order-ID/replay registry or a
historical resubmission guarantee. It does not scan retained order history.

Rule-return admission rechecks non-roll order quantities as finite, nonzero whole
units and `LimitOrder.limit_price` as a finite real number. Booleans and numeric
strings are invalid. An invalid order rejects its entire callback batch before
risk decisions or execution; use valid numeric fields and rerun with a fresh
strategy. Valid edits before submission preserve object identity and metadata,
including finite negative/zero limit prices. This does not freeze numeric fields
after admission or extend validation to standalone risk-policy calls.

`RollOrder.legs()` revalidates contract types, distinct same-group contracts,
and finite nonzero whole quantities with opposite signs at expansion, not only
construction. Only an `OPEN` roll command can expand into fresh market legs.
Rules returning invalid rolls now fail before their batch reaches risk decisions
or accounting; fix the command and rerun with a fresh strategy. Unequal-sized
and valid pre-submission edits remain supported. This does not make roll terms
immutable or add all-or-none execution across custom simulators/risk policies.

Rule and simulator callbacks must preserve pending orders' contract reference,
submission timestamp (value and NumPy unit), and time-in-force. Rules may cancel
pending orders but not resize or fill them. Risk policies must preserve the
protected identity fields, quantities and statuses of proposed/pending orders.
Violations now fail closed; callback failure restores these five fields.
Custom metadata, order-type-specific terms and other external state remain
outside this scoped rollback contract; this is not global order immutability.

## Deprecation implementation

Deprecations must include all of the following:

1. A `DeprecationWarning` with `stacklevel=2` at the old call site.
2. A documented replacement and earliest removal release.
3. Tests for both the warning and the replacement.
4. A changelog entry under a dedicated deprecations heading.
