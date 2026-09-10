import json
from dataclasses import dataclass

from gambit.execution_identity import describe_component
from gambit.risk import MaxOrderQuantity


def test_declared_policy_parameters_are_deterministic_and_distinguishable():
    first = describe_component(MaxOrderQuantity(1))
    assert first == describe_component(MaxOrderQuantity(1))
    assert first != describe_component(MaxOrderQuantity(2))
    assert first["description"]["parameters"]["maximum"] == 1
    assert first["unresolved"]  # source is not a transitive dependency closure


def test_callbacks_and_opaque_state_are_not_claimed_reproducible():
    def callback():
        return 1

    manifest = describe_component(callback)
    assert "component.state" in manifest["unresolved"]
    assert "source_sha256" in manifest["description"]

    class Opaque:
        def __repr__(self):
            raise AssertionError("repr must not run")

    assert describe_component(Opaque())["unresolved"]


def test_cycles_nonfinite_values_and_large_containers_are_bounded():
    cycle = []
    cycle.append(cycle)
    for value in (cycle, float("nan"), list(range(1001))):
        result = describe_component(value)
        assert result["unresolved"]
        json.dumps(result, allow_nan=False)


def test_mutable_dataclass_snapshot_is_detached():
    @dataclass
    class Policy:
        limits: list[int]

    policy = Policy([1])
    first = describe_component(policy)
    policy.limits.append(2)
    assert first["description"]["parameters"] == {"limits": [1]}
    assert first != describe_component(policy)
