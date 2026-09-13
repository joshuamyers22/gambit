"""Contracts for the deterministic financial mutation gate."""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
RUNNER: dict[str, Any] = runpy.run_path(str(ROOT / "tools" / "run_financial_mutations.py"))
MUTATIONS = RUNNER["MUTATIONS"]
Mutation = RUNNER["Mutation"]
apply_mutation = RUNNER["apply_mutation"]
restore_mutation_targets = RUNNER["restore_mutation_targets"]


def test_financial_mutations_are_unique_exact_and_syntactically_valid() -> None:
    assert len(MUTATIONS) == 10
    assert len({mutation.name for mutation in MUTATIONS}) == len(MUTATIONS)
    for mutation in MUTATIONS:
        source = (ROOT / "src" / "gambit" / mutation.module).read_text()
        assert source.count(mutation.before) == 1, mutation.name
        mutated = apply_mutation(source, mutation)
        assert mutated != source
        assert mutation.before not in mutated
        assert mutation.after in mutated


def test_financial_mutation_targets_both_risk_and_accounting() -> None:
    assert {mutation.module for mutation in MUTATIONS} == {"risk.py", "contract_pnl.py"}
    assert all(mutation.tests for mutation in MUTATIONS)
    makefile = (ROOT / "Makefile").read_text()
    assert "mutation-financial:\n\t$(UV_RUN) python tools/run_financial_mutations.py" in makefile
    check_line = next(line for line in makefile.splitlines() if line.startswith("check:"))
    assert "mutation-financial" in check_line


def test_each_financial_mutation_restores_every_target(tmp_path: Path) -> None:
    package = tmp_path / "gambit"
    package.mkdir()
    for module in {mutation.module for mutation in MUTATIONS}:
        (package / module).write_text("contaminated by prior mutant")

    restore_mutation_targets(tmp_path)

    for module in {mutation.module for mutation in MUTATIONS}:
        assert (package / module).read_text() == (
            ROOT / "src" / "gambit" / module
        ).read_text()


@pytest.mark.parametrize("matches", ["", "same same"])
def test_financial_mutation_rejects_missing_or_ambiguous_source(matches: str) -> None:
    mutation = Mutation("invalid", "example.py", "same", "changed", ())
    with pytest.raises(RuntimeError, match="expected one source match"):
        apply_mutation(matches, mutation)
