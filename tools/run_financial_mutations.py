#!/usr/bin/env python3
"""Run deterministic mutation checks for high-consequence financial policy code."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).parents[1]
SOURCE_PACKAGE = ROOT / "src" / "gambit"
RISK_TESTS = (
    "tests/test_risk.py",
    "tests/test_risk_quantity_boundary.py",
    "tests/test_position_fill_sequences.py",
    "tests/test_financial_acceptance.py",
)
PNL_TESTS = (
    "tests/test_accounting_oracle.py",
    "tests/test_financial_acceptance.py",
    "tests/test_stateful_reconciliation.py",
)


class Mutation(NamedTuple):
    name: str
    module: str
    before: str
    after: str
    tests: tuple[str, ...]


MUTATIONS = (
    Mutation(
        "max-order-exact-boundary-rejected",
        "risk.py",
        "if abs(order.qty) > self.maximum:",
        "if abs(order.qty) >= self.maximum:",
        RISK_TESTS,
    ),
    Mutation(
        "positive-position-exact-boundary-rejected",
        "risk.py",
        "upper <= self.maximum if order.qty > 0 else lower >= -self.maximum",
        "upper < self.maximum if order.qty > 0 else lower >= -self.maximum",
        RISK_TESTS,
    ),
    Mutation(
        "negative-position-exact-boundary-rejected",
        "risk.py",
        "upper <= self.maximum if order.qty > 0 else lower >= -self.maximum",
        "upper <= self.maximum if order.qty > 0 else lower > -self.maximum",
        RISK_TESTS,
    ),
    Mutation(
        "pending-position-exposure-ignored",
        "risk.py",
        "if pending.is_open() and pending.contract.symbol == order.contract.symbol:\n"
        "                lower += min(0, pending.qty)\n"
        "                upper += max(0, pending.qty)",
        "if False and pending.is_open() and pending.contract.symbol == order.contract.symbol:\n"
        "                lower += min(0, pending.qty)\n"
        "                upper += max(0, pending.qty)",
        RISK_TESTS,
    ),
    Mutation(
        "accepted-policy-result-treated-as-rejection",
        "risk.py",
        "if not result.accepted:",
        "if result.accepted:",
        RISK_TESTS,
    ),
    Mutation(
        "realized-pnl-contract-multiplier-removed",
        "contract_pnl.py",
        "                self.contract.multiplier,\n            )",
        "                1.0,\n            )",
        PNL_TESTS,
    ),
    Mutation(
        "unrealized-pnl-contract-multiplier-removed",
        "contract_pnl.py",
        "open_qty * price_change * self.contract.multiplier",
        "open_qty * price_change",
        PNL_TESTS,
    ),
    Mutation(
        "commission-added-instead-of-deducted",
        "contract_pnl.py",
        "(realized, unrealized, -commission, -fee)",
        "(realized, unrealized, commission, -fee)",
        PNL_TESTS,
    ),
    Mutation(
        "fee-added-instead-of-deducted",
        "contract_pnl.py",
        "(realized, unrealized, -commission, -fee)",
        "(realized, unrealized, -commission, fee)",
        PNL_TESTS,
    ),
    Mutation(
        "missing-mark-resets-unrealized-pnl",
        "contract_pnl.py",
        "unrealized = prev_unrealized",
        "unrealized = 0.0",
        PNL_TESTS,
    ),
)


def apply_mutation(source: str, mutation: Mutation) -> str:
    """Apply exactly one reviewable source replacement."""
    occurrences = source.count(mutation.before)
    if occurrences != 1:
        raise RuntimeError(
            f"mutation {mutation.name!r} expected one source match in "
            f"{mutation.module}, found {occurrences}"
        )
    mutated = source.replace(mutation.before, mutation.after, 1)
    compile(mutated, mutation.module, "exec")
    return mutated


def restore_mutation_targets(mutant_source: Path) -> None:
    """Restore every mutable target so each campaign case starts pristine."""
    for module in {item.module for item in MUTATIONS}:
        shutil.copy2(SOURCE_PACKAGE / module, mutant_source / "gambit" / module)


def run_mutation(mutation: Mutation, mutant_source: Path) -> tuple[str, str]:
    """Return the mutation outcome and the final pytest summary line."""
    restore_mutation_targets(mutant_source)
    pristine_path = SOURCE_PACKAGE / mutation.module
    mutated_path = mutant_source / "gambit" / mutation.module
    mutated_path.write_text(apply_mutation(pristine_path.read_text(), mutation))

    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    paths = [str(mutant_source)]
    if existing_pythonpath:
        paths.append(existing_pythonpath)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", *mutation.tests],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    summary = next(
        (line for line in reversed(result.stdout.splitlines()) if line.strip()),
        "pytest produced no summary",
    )
    if result.returncode == 1:
        return "killed", summary
    if result.returncode == 0:
        return "survived", summary
    diagnostics = "\n".join(part for part in (result.stdout, result.stderr) if part)
    raise RuntimeError(
        f"mutation {mutation.name!r} produced pytest exit {result.returncode}, "
        f"not an ordinary killed/surviving result:\n{diagnostics}"
    )


def main() -> int:
    survivors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="gambit-financial-mutations-") as directory:
        mutant_source = Path(directory) / "src"
        shutil.copytree(
            SOURCE_PACKAGE,
            mutant_source / "gambit",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for mutation in MUTATIONS:
            outcome, summary = run_mutation(mutation, mutant_source)
            print(f"{outcome.upper():8} {mutation.name}: {summary}")
            if outcome == "survived":
                survivors.append(mutation.name)

    killed = len(MUTATIONS) - len(survivors)
    print(f"financial mutation score: {killed}/{len(MUTATIONS)} killed")
    if survivors:
        print(f"surviving mutations: {', '.join(survivors)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
