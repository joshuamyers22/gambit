.PHONY: sync lock audit lint type test coverage-policy native-warnings docs notebook-clean build check

UV_RUN = uv run --frozen --all-extras

sync:
	uv sync --frozen --all-extras
lock:
	uv lock --check
audit:
	uv export --frozen --all-extras --no-extra dev --no-extra docs --no-dev --no-emit-project | \
		uv tool run pip-audit==2.10.1 --strict --disable-pip --no-deps -r /dev/stdin
lint:
	$(UV_RUN) ruff check src tests tools
type:
	$(UV_RUN) mypy
test:
	$(UV_RUN) pytest --cov=gambit --cov-report=term-missing
coverage-policy:
	$(UV_RUN) python tools/check_coverage_policy.py
native-warnings:
	$(UV_RUN) python tools/check_native_warnings.py
docs:
	$(UV_RUN) python -m sphinx -W --keep-going -b html documentation/source documentation/generated
	$(UV_RUN) python tools/check_clean_paths.py documentation/source
notebook-clean:
	$(UV_RUN) python tools/check_notebook_cleanliness.py
build:
	uv build
	$(UV_RUN) python -m twine check dist/*
	$(UV_RUN) python tools/verify_release_artifacts.py dist
check: lock lint type test coverage-policy native-warnings docs notebook-clean build
