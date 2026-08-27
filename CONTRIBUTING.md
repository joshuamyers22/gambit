# Contributing to Gambit

## Local setup

Gambit requires Python 3.10+ and a C/C++ toolchain. The native CSV reader also
requires libzip (`brew install libzip` on macOS or the equivalent system package).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## Quality checks

```bash
python -m pytest
python -m ruff check src tests
python -m mypy
python -m build
python -m twine check dist/*
```

The CI notebook job executes every example with the notebook extra installed.
The optimizer example uses a one-point smoke grid so it validates the complete
statsmodels/Polars path without turning routine CI into a parameter search.
`data/create_data.ipynb` validates imports only; running `create_data(...)`
requires the maintainer-owned source HDF5 archive.

Correctness changes to accounting, execution, filtering, or analytics must include
a failing-before regression test and reconciliation assertions where applicable.
Do not mix generated documentation or broad formatting changes into correctness
patches.

The current mypy gate covers the build configuration and new regression suite.
Expanding typed coverage across the legacy numerical modules is tracked in the
adversarial review and should be done incrementally without blanket suppressions.

## Repository setup

The canonical repository is `joshuamyers22/gambit`. Create it as a private
GitHub repository, add it as the `origin` remote, and push the default branch.
