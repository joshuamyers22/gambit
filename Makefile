.PHONY: sync lock audit lint type test coverage-policy build check

sync:
	uv sync --frozen --all-extras
lock:
	uv lock --check
audit:
	uv export --frozen --no-dev --no-emit-project | \
		uv tool run pip-audit==2.10.1 --strict --disable-pip --no-deps -r /dev/stdin
lint:
	uv run ruff check src tests tools
type:
	uv run mypy
test:
	uv run pytest --cov=gambit --cov-report=term-missing
coverage-policy:
	uv run python tools/check_coverage_policy.py
build:
	uv build
check: lock lint type test coverage-policy build
