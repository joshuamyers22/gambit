from __future__ import annotations

import json
import re
from importlib.metadata import version
from pathlib import Path

import gambit


def test_distribution_and_import_versions_match() -> None:
    assert gambit.__version__ == version("gambit-markets") == "1.0.0"


def test_root_api_is_explicit_and_free_of_dependency_leaks() -> None:
    expected = {
        "Account",
        "BacktestResult",
        "Contract",
        "ContractGroup",
        "MarketOrder",
        "Strategy",
        "StrategyBuilder",
        "has_display",
        "np_find_closest",
        "set_defaults",
        "validate_market_data",
    }

    assert expected <= set(gambit.__all__)
    assert not {"Any", "Callable", "SimpleNamespace", "np", "pl", "math", "dataclass", "foo"} & set(gambit.__all__)
    assert all(not name.startswith("test_") for name in gambit.__all__)
    assert sorted(gambit.__all__) == sorted(set(gambit.__all__))


def test_option_rho_is_bound_to_rho_formula() -> None:
    value = gambit.rho(True, 100.0, 100.0, 1.0, 0.05, 0.2)

    assert 0.5 < value < 0.6


def test_notebook_root_api_usage_is_declared() -> None:
    notebooks = Path(__file__).parents[1] / "examples" / "notebooks"
    referenced_names: set[str] = set()
    for path in notebooks.rglob("*.ipynb"):
        document = json.loads(path.read_text())
        for cell in document.get("cells", []):
            if cell.get("cell_type") == "code":
                source = "".join(cell.get("source", []))
                referenced_names.update(re.findall(r"\bpq\.([A-Za-z_]\w*)", source))

    assert referenced_names <= set(gambit.__all__)
