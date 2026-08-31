"""Bulk construction of validated contract universes."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType, SimpleNamespace

import numpy as np

from gambit.instruments import InstrumentSpec
from gambit.pq_types import Contract, ContractGroup


@dataclass(frozen=True)
class ContractSpec:
    """Overrides for one contract in a bulk-created group."""

    symbol: str
    expiry: np.datetime64 | None = None
    multiplier: float | None = None
    instrument_spec: InstrumentSpec | None = None
    properties: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ContractGroupSpec:
    """Contracts and shared defaults for one logical instrument group."""

    contracts: Iterable[str | ContractSpec]
    multiplier: float = 1.0
    instrument_spec: InstrumentSpec | None = None


@dataclass(frozen=True)
class ContractUniverse:
    """Read-only indexes returned by :func:`create_contract_groups`."""

    groups: Mapping[str, ContractGroup]
    contracts: Mapping[str, Contract]

    def group(self, name: str) -> ContractGroup:
        return self.groups[name]

    def contract(self, symbol: str) -> Contract:
        return self.contracts[symbol]


@dataclass(frozen=True)
class _PreparedContract:
    group_name: str
    symbol: str
    expiry: np.datetime64 | None
    multiplier: float
    instrument_spec: InstrumentSpec
    properties: Mapping[str, object] | None


def _prepare_contracts(
    definitions: Mapping[str, Iterable[str | ContractSpec] | ContractGroupSpec],
) -> list[_PreparedContract]:
    if not isinstance(definitions, Mapping):
        raise TypeError("contract-group definitions must be a mapping")
    prepared: list[_PreparedContract] = []
    requested_symbols: set[str] = set()

    for group_name, definition in definitions.items():
        if not isinstance(group_name, str) or not group_name.strip():
            raise ValueError("contract-group names must be non-empty strings")
        if isinstance(definition, ContractGroupSpec):
            entries = definition.contracts
            default_multiplier = definition.multiplier
            default_instrument = definition.instrument_spec or InstrumentSpec()
        else:
            entries = definition
            default_multiplier = 1.0
            default_instrument = InstrumentSpec()
        if not isinstance(default_instrument, InstrumentSpec):
            raise TypeError(f"instrument_spec for {group_name} must be an InstrumentSpec")
        if isinstance(entries, (str, bytes)):
            raise TypeError(f"contracts for {group_name} must be an iterable, not a string")

        for entry in entries:
            spec = ContractSpec(entry) if isinstance(entry, str) else entry
            if not isinstance(spec, ContractSpec):
                raise TypeError(f"contract definition in {group_name} must be a symbol or ContractSpec")
            if not isinstance(spec.symbol, str) or not spec.symbol:
                raise ValueError("contract symbols must be non-empty strings")
            if spec.symbol in requested_symbols:
                raise ValueError(f"duplicate contract symbol in bulk request: {spec.symbol}")
            if Contract.exists(spec.symbol):
                raise ValueError(f"contract already exists: {spec.symbol}")
            multiplier = default_multiplier if spec.multiplier is None else spec.multiplier
            if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
                raise TypeError(f"multiplier for {spec.symbol} must be numeric")
            if not math.isfinite(multiplier) or multiplier <= 0:
                raise ValueError(f"multiplier for {spec.symbol} must be finite and positive")
            if spec.expiry is not None:
                if not isinstance(spec.expiry, np.datetime64):
                    raise TypeError(f"expiry for {spec.symbol} must be numpy.datetime64")
                if np.isnat(spec.expiry):
                    raise ValueError(f"expiry for {spec.symbol} cannot be NaT")
            if spec.properties is not None and not isinstance(spec.properties, Mapping):
                raise TypeError(f"properties for {spec.symbol} must be a mapping")
            if spec.properties is not None and not all(isinstance(key, str) for key in spec.properties):
                raise TypeError(f"property names for {spec.symbol} must be strings")
            instrument_spec = spec.instrument_spec or default_instrument
            if not isinstance(instrument_spec, InstrumentSpec):
                raise TypeError(f"instrument_spec for {spec.symbol} must be an InstrumentSpec")
            requested_symbols.add(spec.symbol)
            prepared.append(
                _PreparedContract(
                    group_name=group_name,
                    symbol=spec.symbol,
                    expiry=spec.expiry,
                    multiplier=float(multiplier),
                    instrument_spec=instrument_spec,
                    properties=spec.properties,
                )
            )
    return prepared


def create_contract_groups(
    definitions: Mapping[str, Iterable[str | ContractSpec] | ContractGroupSpec],
) -> ContractUniverse:
    """Create many groups and contracts after validating the entire request.

    The global registries are not changed when preflight validation fails.
    Symbols remain globally unique, matching :meth:`Contract.create` semantics.
    Existing empty or populated groups may receive additional new contracts.
    """
    prepared = _prepare_contracts(definitions)
    groups = {name: ContractGroup.get(name) for name in definitions}
    contracts: dict[str, Contract] = {}
    for item in prepared:
        properties = SimpleNamespace(**dict(item.properties)) if item.properties is not None else None
        contracts[item.symbol] = Contract.create(
            item.symbol,
            contract_group=groups[item.group_name],
            expiry=item.expiry,
            multiplier=item.multiplier,
            properties=properties,
            instrument_spec=item.instrument_spec,
        )
    return ContractUniverse(MappingProxyType(groups), MappingProxyType(contracts))
