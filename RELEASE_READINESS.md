# Release readiness

Status: **beta release candidate 1.1.0; production qualification and TestPyPI validation pending**.

This status must be read with the canonical
[feature-status matrix](FEATURE_STATUS.md) and the draft
[project brief](PROJECT_BRIEF.md). A public API compatibility commitment does
not promote experimental capabilities or make the distribution
production/stable. Restoring a stable classifier requires approval of every P0
gate in [PRODUCTION_READINESS_PLAN.md](PRODUCTION_READINESS_PLAN.md).

Hardening through 2026-09-12 added independently expected financial acceptance,
targeted mutation testing, and a historical-output disposition policy. Required
[CI run 34706032394](https://github.com/joshuamyers22/gambit/actions/runs/34706032394)
passed the current financial suite on Linux/macOS and CPython 3.10–3.12 at
`888e670`. That run establishes same-commit CI evidence but does not replace the
separate non-publishing release workflow or the external approval steps below.

The repository now has automated release-candidate gates for the supported
CPython 3.10-3.12 matrix on Linux x86_64 and macOS x86_64/arm64. Each wheel is
tested by cibuildwheel after installation. The combined artifact set is then
checked for metadata, licenses, dependencies, expected native modules, platform
coverage, and accidental development/source files. A compatible wheel is
installed outside the checkout into separate clean environments for core,
`persistence`, `calendars`, `research`, `visualization`, and `notebooks` use.
The sdist is built and installed independently. TestPyPI upload requires an
explicit workflow input and production PyPI remains restricted to a published
GitHub release whose tag matches the package version.

The first hosted matrix runs exposed two infrastructure constraints: current
Homebrew native bottles require the builder's macOS deployment target, and the
former macOS 13 Intel runner has been removed. Both macOS wheel lanes now use
supported macOS 15 runners and declare a macOS 15 minimum so repaired wheels
cannot advertise compatibility their bundled libraries do not provide.

The final non-publishing hosted release run `33439901354` passed on 2026-08-31.
It built all nine expected wheels and the sdist, passed the combined archive and
metadata audit, and completed isolated core, optional-extra, CLI, native, and
sdist installation checks. Both publishing jobs were skipped.

## Remaining release decisions and external checks

- Inventory and approve every pre-correction result under
  [HISTORICAL_OUTPUT_DISPOSITION.md](HISTORICAL_OUTPUT_DISPOSITION.md). The
  repository supplies correction rules and a register template but contains no
  owner-controlled external result inventory.
- Confirm the `testpypi` and `pypi` GitHub environments, maintainer approval,
  and Trusted Publisher records described in `RELEASING.md`. These settings
  cannot be proven from the repository checkout.
- Run the manual release workflow with TestPyPI publishing enabled and verify
  direct wheel installation from TestPyPI on clean Linux and macOS hosts.
- Review the TestPyPI project page rendering, file list, metadata, license,
  dependency declarations, provenance, and attestations before creating a
  signed production tag.

Option pricing, implied volatility, and expiry/settlement behavior are
experimental and not part of the production-qualified candidate boundary. They
must pass P1.4 before those capabilities are represented as production-ready.
