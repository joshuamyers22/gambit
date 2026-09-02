import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "gambit"
STABLE_KERNEL = {
    "boundaries",
    "calculation",
    "factor_identity",
    "instruments",
    "market_data",
}


def _gambit_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if alias.name == "gambit" or alias.name.startswith("gambit."))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "gambit" or node.module.startswith("gambit."):
                imports.add(node.module)
    return imports


def test_stable_contract_kernel_does_not_depend_on_outer_package_modules() -> None:
    violations = {}
    for module in sorted(STABLE_KERNEL):
        imports = _gambit_imports(PACKAGE_ROOT / f"{module}.py")
        if imports:
            violations[module] = sorted(imports)

    assert violations == {}, f"stable contract modules must remain dependency-free: {violations}"


def test_strategy_components_do_not_depend_on_strategy_orchestrator() -> None:
    imports = _gambit_imports(PACKAGE_ROOT / "strategy_components.py")

    assert "gambit.strategy" not in imports


def test_strategy_orchestrator_does_not_import_evaluator_adapter() -> None:
    imports = _gambit_imports(PACKAGE_ROOT / "strategy.py")

    assert "gambit.evaluator" not in imports


def test_trade_reconciliation_does_not_depend_on_account_aggregate() -> None:
    imports = _gambit_imports(PACKAGE_ROOT / "trade_reconciliation.py")

    assert "gambit.account" not in imports


def test_contract_pnl_ledger_does_not_depend_on_account_aggregate() -> None:
    imports = _gambit_imports(PACKAGE_ROOT / "contract_pnl.py")

    assert "gambit.account" not in imports


def test_account_aggregate_does_not_depend_directly_on_native_pnl_kernel() -> None:
    imports = _gambit_imports(PACKAGE_ROOT / "account.py")

    assert "gambit.compute_pnl" not in imports


def test_factor_store_runtime_invariants_do_not_use_optimizable_asserts() -> None:
    path = PACKAGE_ROOT / "factor_store.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
