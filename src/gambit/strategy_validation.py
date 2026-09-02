"""Pure validation policies for strategy stage configuration and selection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from gambit.pq_types import ContractGroup


def validated_stage_groups(
    contract_groups: Sequence[ContractGroup] | None,
    configured_groups: tuple[ContractGroup, ...],
    *,
    operation: str,
) -> tuple[ContractGroup, ...]:
    """Return configured group identities selected for one stage operation."""
    if contract_groups is None:
        return configured_groups
    if isinstance(contract_groups, (str, bytes)) or not isinstance(contract_groups, Sequence):
        raise TypeError(f"{operation} contract_groups must be a sequence of ContractGroup objects")
    groups = tuple(contract_groups)
    if not groups:
        raise ValueError(f"{operation} requires at least one contract group")
    if not all(isinstance(group, ContractGroup) for group in groups):
        raise TypeError(f"{operation} contract_groups must contain only ContractGroup objects")
    for group in groups:
        if not any(group is configured for configured in configured_groups):
            raise ValueError(f"contract group {group.name!r} is not configured for this strategy")
    return groups


def validated_stage_names(
    names: Sequence[str] | None,
    registered_names: Sequence[str],
    *,
    operation: str,
) -> tuple[str, ...]:
    """Validate a selective stage request while preserving its deterministic order."""
    if names is None:
        return tuple(registered_names)
    if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
        raise TypeError(f"{operation} names must be a sequence of strings")
    if not all(isinstance(name, str) for name in names):
        raise TypeError(f"{operation} names must contain only strings")
    unknown = [name for name in names if name not in registered_names]
    if unknown:
        raise ValueError(f"unknown {operation} names: {', '.join(unknown)}")
    return tuple(dict.fromkeys(names))


def validate_dependency_scopes(
    indicator_groups: Mapping[str, Sequence[ContractGroup]],
    indicator_dependencies: Mapping[str, Sequence[str]],
    signal_groups: Mapping[str, Sequence[ContractGroup]],
    signal_indicator_dependencies: Mapping[str, Sequence[str]],
    signal_dependencies: Mapping[str, Sequence[str]],
) -> None:
    """Require every dependency to cover each group that consumes it."""
    for name, groups in indicator_groups.items():
        for dependency in indicator_dependencies[name]:
            _require_group_coverage("indicator", name, "indicator", dependency, groups, indicator_groups[dependency])
    for name, groups in signal_groups.items():
        for dependency in signal_indicator_dependencies[name]:
            _require_group_coverage("signal", name, "indicator", dependency, groups, indicator_groups[dependency])
        for dependency in signal_dependencies[name]:
            _require_group_coverage("signal", name, "signal", dependency, groups, signal_groups[dependency])


def _require_group_coverage(
    consumer_kind: str,
    consumer: str,
    dependency_kind: str,
    dependency: str,
    consumer_groups: Sequence[ContractGroup],
    dependency_groups: Sequence[ContractGroup],
) -> None:
    for group in consumer_groups:
        if not any(group is dependency_group for dependency_group in dependency_groups):
            raise ValueError(
                f"{consumer_kind} {consumer!r} depends on {dependency_kind} {dependency!r}, which is not registered "
                f"for contract group {group.name}"
            )


__all__ = ["validate_dependency_scopes", "validated_stage_groups", "validated_stage_names"]
