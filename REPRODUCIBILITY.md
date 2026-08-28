# Reproducibility

The Python runtime is declared in `.python-version`; dependency resolution is
captured in `uv.lock`. Install with `uv sync --frozen --all-extras` and run the
complete local quality gate with `make check`.

Update dependencies deliberately with `uv lock --upgrade`, review both
`pyproject.toml` and `uv.lock`, and rerun `make check`. Tests must use fixed
fixtures and must not contact live services unless explicitly marked. Local
credentials and machine-specific data belong in ignored files, never Git.

Build artifacts are reproduced with `uv build`. Generated `dist/`, coverage,
cache, and virtual-environment directories are not source artifacts.
