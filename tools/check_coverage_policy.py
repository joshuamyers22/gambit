"""Enforce coverage floors for supported policy-heavy modules."""

from __future__ import annotations

import io
from pathlib import Path

from coverage import Coverage
from coverage.exceptions import CoverageException

REPOSITORY_ROOT = Path(__file__).parents[1]
MODULE_FLOORS = {
    "src/gambit/markets.py": 75.0,
    "src/gambit/holiday_calendars.py": 50.0,
    "src/gambit/optimize.py": 55.0,
    "src/gambit/pq_utils.py": 35.0,
}


def main() -> int:
    coverage = Coverage(data_file=str(REPOSITORY_ROOT / ".coverage"))
    coverage.load()
    failures = []
    for relative_path, floor in MODULE_FLOORS.items():
        path = REPOSITORY_ROOT / relative_path
        try:
            measured = coverage.report(morfs=[str(path)], file=io.StringIO())
        except CoverageException as error:
            failures.append(f"{relative_path}: unavailable ({error})")
            continue
        print(f"{relative_path}: {measured:.2f}% (minimum {floor:.2f}%)")
        if measured < floor:
            failures.append(f"{relative_path}: {measured:.2f}% is below {floor:.2f}%")

    if failures:
        print("Coverage policy failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
