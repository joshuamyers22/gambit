"""Validate that committed notebooks contain source only, without run artifacts."""

from __future__ import annotations

from pathlib import Path

import nbformat

ROOT = Path(__file__).parents[1] / "examples" / "notebooks"


def main() -> None:
    failures: list[str] = []
    notebooks = sorted(ROOT.rglob("*.ipynb"))
    if not notebooks:
        raise SystemExit("no example notebooks found")
    for path in notebooks:
        notebook = nbformat.read(path, as_version=4)
        try:
            nbformat.validate(notebook)
        except nbformat.ValidationError as exc:
            failures.append(f"{path.relative_to(ROOT)}: invalid notebook: {exc.message}")
            continue
        for index, cell in enumerate(notebook.cells):
            if cell.cell_type != "code":
                continue
            if cell.execution_count is not None:
                failures.append(f"{path.relative_to(ROOT)}: cell {index} has an execution count")
            if cell.outputs:
                failures.append(f"{path.relative_to(ROOT)}: cell {index} has committed output")
    if failures:
        raise SystemExit("notebook cleanliness check failed:\n" + "\n".join(failures))


if __name__ == "__main__":
    main()
