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

## Experimental native APIs

`MappedFloat64Column`, `TickRing`, and `TickFactorProcessor` remain development
prototypes. Their presence in the root namespace makes discovery convenient but
does not make their storage layout or concurrency contract stable. Production
promotion requires the correctness, sanitizer, crash-recovery, and performance
gates in `ADVERSARIAL_REVIEW_PLAN.md`.

## Deprecation implementation

Deprecations must include all of the following:

1. A `DeprecationWarning` with `stacklevel=2` at the old call site.
2. A documented replacement and earliest removal release.
3. Tests for both the warning and the replacement.
4. A changelog entry under a dedicated deprecations heading.

