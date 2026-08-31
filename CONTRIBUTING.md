# Contributing to Gambit

## Local setup

Gambit requires Python 3.10+ and a C/C++ toolchain. The native CSV reader also
requires libzip (`brew install libzip` on macOS or the equivalent system package).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,docs,notebooks]"
```

## Quality checks

```bash
python -m pytest
python -m ruff check src tests
python -m mypy
python tools/check_notebook_cleanliness.py
python tools/migrate_notebooks.py
python tools/check_clean_paths.py examples/notebooks
python -m sphinx -W --keep-going -b html documentation/source documentation/generated
python tools/check_clean_paths.py documentation/source
python -m build
python -m twine check dist/*
```

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

The current mypy gate covers the build configuration and new regression suite.
Expanding typed coverage across the legacy numerical modules is tracked in the
adversarial review and should be done incrementally without blanket suppressions.

## Repository and release setup

The canonical public repository is `joshuamyers22/gambit`. Pull requests must
pass tests, lint, typing, native sanitizer jobs, notebook execution, package
builds, and warning-free documentation builds.

Releases use the `gambit-markets` distribution name and retain the `gambit`
import namespace. A manual release-workflow run publishes to TestPyPI. A
published GitHub release publishes the previously verified artifacts to PyPI by
Trusted Publishing; maintainers must never upload local `dist/` contents. See
`RELEASING.md` for environment setup, TestPyPI validation, and the release
checklist.
