"""Fail when tracked or untracked files changed beneath selected paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def main() -> None:
    paths = sys.argv[1:]
    if not paths:
        raise SystemExit("usage: check_clean_paths.py PATH [PATH ...]")
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        raise SystemExit("generated artifacts are out of date:\n" + result.stdout)


if __name__ == "__main__":
    main()
