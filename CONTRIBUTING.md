# Contributing to Gambit

## Local setup

Gambit supports Python 3.10–3.12 and requires uv and a C/C++ toolchain. The native CSV reader also
requires libzip (`brew install libzip` on macOS or the equivalent system package).

```bash
make sync
source .venv/bin/activate
```

## Quality checks

```bash
make check
make audit
```

`make check` includes lint, typing, the full test suite and coverage floors,
native compiler warnings, strict documentation and source-drift checks, notebook
cleanliness, builds, and distribution metadata/content checks. Formatting is not
enforced across the legacy tree; preserve nearby style. `make audit` uses the
network and checks locked core and optional runtime dependencies, excluding the
dev and docs extras. Keep old release artifacts outside `dist/` before building.

CI installs all extras from the frozen lock on its supported runtime matrix.
The CI notebook job executes every example with the notebook extra installed.
The optimizer example uses a one-point smoke grid so it validates the complete
statsmodels/Polars path without turning routine CI into a parameter search.
`data/create_data.ipynb` validates imports only; running `create_data(...)`
requires the maintainer-owned source HDF5 archive.

Committed notebooks contain source only: execution counts and outputs are build
artifacts. After changing a notebook, run the migration/normalization command and
commit its deterministic result. CI executes every notebook, normalizes it again,
and rejects any remaining source or metadata difference. The strict Sphinx build
similarly must not mutate or create files under `documentation/source`.

Correctness changes to accounting, execution, filtering, or analytics must include
a failing-before regression test and reconciliation assertions where applicable.
Do not mix generated documentation or broad formatting changes into correctness
patches.

The mypy file list in `pyproject.toml` covers the maintained policy modules,
build configuration, and selected regressions. Expand typed boundaries
incrementally without blanket suppressions.

## Repository and release setup

The canonical public repository is `joshuamyers22/gambit`. Pull requests must
pass tests, lint, typing, native sanitizer jobs, notebook execution, package
builds, and warning-free documentation builds.

Releases use the `gambit-markets` distribution name and retain the `gambit`
import namespace. A manual release-workflow run publishes to TestPyPI only when
explicitly enabled. Both publishing paths require the complete CI workflow for
the same source commit plus artifact verification. A published GitHub release
builds and verifies its artifacts before publishing through Trusted Publishing;
maintainers must never upload local `dist/` contents. See
`RELEASING.md` for environment setup, TestPyPI validation, and the release
checklist.
