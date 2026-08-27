#!/usr/bin/env bash
set -euo pipefail

python -m ruff check src tests
python -m mypy
python -m pytest
python -m sphinx -W documentation/source documentation/generated
python -m build
python -m twine check dist/*
