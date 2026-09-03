.PHONY: sync lock lint type test build check

sync:
	uv sync --frozen --all-extras
lock:
	uv lock --check
lint:
	uv run ruff check src tests
type:
	uv run mypy
test:
	uv run pytest --cov=gambit --cov-report=term-missing
build:
	uv build
check: lock lint type test build
