import pytest

from gambit.pq_types import Contract, ContractGroup


@pytest.fixture(autouse=True)
def clear_contract_registries():
    """Keep globally cached domain objects from leaking between tests."""
    Contract.clear_cache()
    ContractGroup.clear_cache()
    yield
    Contract.clear_cache()
    ContractGroup.clear_cache()
