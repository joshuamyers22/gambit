"""Keep the public risk recipes executable as the APIs evolve."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE_DIRECTORY = Path(__file__).parents[1] / "examples" / "risk"
RISK_EXAMPLES = (
    "portfolio_exposure.py",
    "stress_scenarios.py",
    "typed_measures.py",
    "pre_trade_controls.py",
)


@pytest.mark.parametrize("script_name", RISK_EXAMPLES)
def test_risk_example_runs(script_name: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(EXAMPLE_DIRECTORY / script_name)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip()
