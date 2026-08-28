.PHONY: sync lint type test build check

sync:
	uv sync --frozen --all-extras
lint:
	uv run ruff check src tests
type:
	uv run mypy
test:
	uv run pytest --cov=gambit --cov-report=term-missing
build:
	uv build
check: lint type test build
