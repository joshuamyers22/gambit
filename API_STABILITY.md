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

## Callback order ownership

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
