from __future__ import annotations

import math

import pytest

from gambit.factor_identity import FactorColumnSchema, FactorNodeIdentity

INPUT_A = "a" * 64
INPUT_B = "b" * 64
PARENT_A = "c" * 64


def _identity(**changes) -> FactorNodeIdentity:
    arguments = {
        "transform": "research.signals.zscore",
        "transform_version": "2.1.0",
        "parents": (PARENT_A,),
        "input_fingerprints": {"prices": INPUT_A, "calendar": INPUT_B},
        "parameters": {"window": 20, "center": False, "weights": [1.0, 2.0]},
        "output_schema": (FactorColumnSchema("zscore", "float64", nullable=True),),
        "row_ordering": ("instrument_id", "timestamp_ns"),
        "research_context": {"calendar": "XNYS", "floating_point": "strict"},
    }
    arguments.update(changes)
    return FactorNodeIdentity(**arguments)


def test_factor_identity_is_deterministic_and_mapping_order_independent() -> None:
    first = _identity()
    reordered = _identity(
        parameters={"weights": [1.0, 2.0], "center": False, "window": 20},
        input_fingerprints={"calendar": INPUT_B, "prices": INPUT_A},
        research_context={"floating_point": "strict", "calendar": "XNYS"},
    )

    assert first.node_key == reordered.node_key
    assert first.snapshot() == reordered.snapshot()


@pytest.mark.parametrize(
    "change",
    [
        {"parents": ("d" * 64,)},
        {"input_fingerprints": {"prices": "e" * 64}},
        {"transform": "research.signals.rank"},
        {"transform_version": "2.2.0"},
        {"parameters": {"window": 21}},
        {"output_schema": (FactorColumnSchema("zscore", "float32", nullable=True),)},
        {"row_ordering": ("timestamp_ns", "instrument_id")},
        {"research_context": {"calendar": "24/7", "floating_point": "strict"}},
    ],
)
def test_factor_identity_invalidates_on_every_contract_dimension(change) -> None:
    assert _identity(**change).node_key != _identity().node_key


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"parents": ("not-a-digest",)}, "SHA-256"),
        ({"input_fingerprints": {}}, "at least one parent or input"),
        ({"output_schema": ()}, "must not be empty"),
        ({"row_ordering": ()}, "explicitly name"),
        ({"parameters": {"threshold": math.nan}}, "non-finite"),
        ({"parameters": {"unsupported": {1, 2}}}, "unsupported value type"),
    ],
)
def test_factor_identity_rejects_ambiguous_or_unstable_inputs(change, message) -> None:
    arguments = {"parents": (), "input_fingerprints": {"prices": INPUT_A}, **change}
    with pytest.raises((TypeError, ValueError), match=message):
        _identity(**arguments)


def test_factor_identity_preserves_parent_order() -> None:
    first = _identity(parents=(PARENT_A, "d" * 64))
    second = _identity(parents=("d" * 64, PARENT_A))

    assert first.node_key != second.node_key


def test_factor_identity_is_detached_from_mutable_constructor_inputs() -> None:
    parameters = {"window": 20}
    inputs = {"prices": INPUT_A}
    identity = _identity(parameters=parameters, input_fingerprints=inputs)
    original_key = identity.node_key
    original_snapshot = identity.snapshot()

    parameters["window"] = 100
    inputs["prices"] = INPUT_B

    assert identity.node_key == original_key
    assert identity.snapshot() == original_snapshot


def test_factor_identity_round_trips_persisted_snapshot() -> None:
    identity = _identity()

    restored = FactorNodeIdentity.from_snapshot(identity.snapshot())

    assert restored.snapshot() == identity.snapshot()
    assert restored.node_key == identity.node_key


def test_factor_identity_rejects_unknown_persisted_fields() -> None:
    snapshot = _identity().snapshot()
    snapshot["unexpected"] = True

    with pytest.raises(ValueError, match="fields are invalid"):
        FactorNodeIdentity.from_snapshot(snapshot)


def test_factor_identity_rejects_boolean_snapshot_version() -> None:
    snapshot = _identity().snapshot()
    snapshot["version"] = True

    with pytest.raises(ValueError, match="version is invalid"):
        FactorNodeIdentity.from_snapshot(snapshot)
