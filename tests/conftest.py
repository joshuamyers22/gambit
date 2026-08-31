import pytest

from gambit.pq_types import Contract, ContractGroup

INTEGRATION_MODULES = {
    "test_backtest_result.py",
    "test_hdf5_hardening.py",
    "test_moving_average_crossover.py",
    "test_optional_dependencies.py",
    "test_polars_integration.py",
    "test_risk_examples.py",
    "test_strategy_golden.py",
}
NATIVE_MODULES = {
    "test_accounting_oracle.py",
    "test_factor_cache.py",
    "test_factor_cli.py",
    "test_factor_dag.py",
    "test_factor_operations.py",
    "test_factor_store.py",
    "test_native_io_hardening.py",
    "test_native_reference.py",
    "test_tick_ring.py",
}
SUITE_MARKERS = {"unit", "integration", "native", "fuzz", "performance"}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Assign every test to one or more documented execution suites."""
    for item in items:
        filename = item.path.name
        if filename.endswith("_benchmark.py"):
            item.add_marker(pytest.mark.performance)
        if filename == "test_boundary_fuzz.py":
            item.add_marker(pytest.mark.fuzz)
            item.add_marker(pytest.mark.native)
        if filename in NATIVE_MODULES:
            item.add_marker(pytest.mark.native)
        if filename in INTEGRATION_MODULES:
            item.add_marker(pytest.mark.integration)
        if not any(item.get_closest_marker(marker) for marker in SUITE_MARKERS):
            item.add_marker(pytest.mark.unit)


@pytest.fixture(autouse=True)
def clear_contract_registries():
    """Keep globally cached domain objects from leaking between tests."""
    Contract.clear_cache()
    ContractGroup.clear_cache()
    yield
    Contract.clear_cache()
    ContractGroup.clear_cache()
