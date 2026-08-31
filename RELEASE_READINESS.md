# Release readiness

Status: **not ready to publish**.

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

## Remaining release decisions and external checks

- Choose the next release version, update both `pyproject.toml` and
  `version.txt`, and move the substantial `Unreleased` changelog into a dated
  section. Publishing the current `1.0.0` metadata would misrepresent these
  post-1.0 changes.
- Confirm the `testpypi` and `pypi` GitHub environments, maintainer approval,
  and Trusted Publisher records described in `RELEASING.md`. These settings
  cannot be proven from the repository checkout.
- Run the manual release workflow once with publishing disabled. After its
  complete matrix passes, run it with TestPyPI publishing enabled and verify
  direct wheel installation from TestPyPI on clean Linux and macOS hosts.
- Review the TestPyPI project page rendering, file list, metadata, license,
  dependency declarations, provenance, and attestations before creating a
  signed production tag.

Option pricing and implied-volatility reference validation remains explicitly
deferred by scope and is not part of this candidate. It must receive its own
validation work before those capabilities are represented as production-ready.
