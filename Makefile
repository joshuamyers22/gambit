.PHONY: sync lock audit lint type test build check

sync:
	uv sync --frozen --all-extras
lock:
	uv lock --check
audit:
	uv export --frozen --no-dev --no-emit-project | \
		uv tool run pip-audit==2.10.1 --strict --disable-pip --no-deps -r /dev/stdin
lint:
	uv run ruff check src tests
type:
	uv run mypy
test:
	uv run pytest --cov=gambit --cov-report=term-missing
build:
	uv build
check: lock lint type test build
