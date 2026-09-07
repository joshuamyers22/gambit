#!/usr/bin/env bash
set -euo pipefail

python -m build
python -m twine check dist/*

echo "Artifacts are ready in dist/. Publish through the verified GitHub release workflow; see RELEASING.md."
