from types import MappingProxyType

import numpy as np
import pytest

from gambit.instruments import AssetClass, InstrumentSpec
from gambit.pq_types import Contract, ContractGroup
from gambit.strategy_builder import StrategyBuilder
from gambit.universe import ContractGroupSpec, ContractSpec, create_contract_groups


def test_create_sector_universe_with_shared_and_per_contract_metadata() -> None:
    equity = InstrumentSpec(asset_class=AssetClass.EQUITY, exchange_calendar="NYSE")
    future = InstrumentSpec(asset_class=AssetClass.FUTURE, exchange_calendar="CME_Equity")

    universe = create_contract_groups(
        {
            "equities/technology": ContractGroupSpec(["AAPL", "MSFT", "NVDA"], instrument_spec=equity),
            "futures/equity-index": ContractGroupSpec(
                [
                    ContractSpec("ESZ6", expiry=np.datetime64("2026-12-18")),
                    ContractSpec("NQZ6", expiry=np.datetime64("2026-12-18"), multiplier=20),
                ],
                multiplier=50,
                instrument_spec=future,
            ),
        }
    )

    assert list(universe.groups) == ["equities/technology", "futures/equity-index"]
    assert list(universe.group("equities/technology").contracts) == ["AAPL", "MSFT", "NVDA"]
    assert universe.contract("AAPL").instrument_spec.asset_class is AssetClass.EQUITY
    assert universe.contract("ESZ6").multiplier == 50
    assert universe.contract("NQZ6").multiplier == 20
    assert universe.contract("NQZ6").contract_group is universe.group("futures/equity-index")
    assert isinstance(universe.groups, MappingProxyType)
    assert isinstance(universe.contracts, MappingProxyType)


def test_bulk_preflight_failure_does_not_mutate_registries() -> None:
    existing_group = ContractGroup.get("existing")
    existing = Contract.create("EXISTING", existing_group)

    with pytest.raises(ValueError, match="duplicate contract symbol"):
        create_contract_groups({"new-sector": ["NEW", "NEW"]})

    assert not ContractGroup.exists("new-sector")
    assert Contract.get("EXISTING") is existing
    assert existing_group.get_contracts() == [existing]


def test_existing_symbol_collision_fails_before_creating_groups() -> None:
    Contract.create("AAPL")

    with pytest.raises(ValueError, match="already exists"):
        create_contract_groups({"equities/technology": ["MSFT", "AAPL"]})

    assert Contract.get("MSFT") is None
    assert not ContractGroup.exists("equities/technology")


def test_bulk_creation_scales_to_thousands_and_preserves_order() -> None:
    symbols = [f"STOCK-{index:04d}" for index in range(2_000)]

    universe = create_contract_groups({"equities/broad": symbols})

    assert len(universe.contracts) == 2_000
    assert list(universe.group("equities/broad").contracts) == symbols


def test_builder_registers_all_universe_groups() -> None:
    universe = create_contract_groups({"equities/energy": ["XOM", "CVX"], "equities/banks": ["JPM", "BAC"]})
    builder = StrategyBuilder()

    builder.add_contract_universe(universe)

    assert list(builder.contract_groups) == ["equities/energy", "equities/banks"]
