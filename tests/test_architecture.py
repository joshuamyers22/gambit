import ast
from pathlib import Path

import yaml

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "gambit"
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]
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


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects mappings whose keys would be overwritten."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


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


def test_strategy_inputs_do_not_depend_on_strategy_components() -> None:
    imports = _gambit_imports(PACKAGE_ROOT / "strategy_inputs.py")

    assert "gambit.strategy_components" not in imports


def test_account_aggregate_does_not_depend_directly_on_native_pnl_kernel() -> None:
    imports = _gambit_imports(PACKAGE_ROOT / "account.py")

    assert "gambit.compute_pnl" not in imports


def test_runtime_invariants_do_not_use_optimizable_asserts() -> None:
    violations = {}
    for path in sorted(PACKAGE_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)]
        if lines:
            violations[path.name] = lines

    assert violations == {}, f"runtime invariants must use explicit exceptions: {violations}"


def test_github_workflows_do_not_contain_shadowed_yaml_keys() -> None:
    workflow_root = REPOSITORY_ROOT / ".github" / "workflows"
    for path in sorted(workflow_root.glob("*.yml")):
        with path.open(encoding="utf-8") as workflow:
            yaml.load(workflow, Loader=_UniqueKeyLoader)
