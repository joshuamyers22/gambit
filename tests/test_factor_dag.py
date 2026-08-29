from __future__ import annotations

import math
from collections.abc import Mapping

import polars as pl
import pytest

import gambit.factor_dag as factor_dag_module
from gambit.factor_cache import MappedFloat64Column
from gambit.factor_dag import FactorCacheAdmissionPolicy, PolarsFactorDagExecutor, PolarsFactorNode
from gambit.factor_identity import FactorColumnSchema, FactorNodeIdentity

pytestmark = pytest.mark.native
INPUT_KEY = "a" * 64


def _identity(
    name: str,
    *,
    parents: tuple[str, ...] = (),
    version: str = "1",
) -> FactorNodeIdentity:
    return FactorNodeIdentity(
        transform=f"tests.{name}",
        transform_version=version,
        parents=parents,
        input_fingerprints={} if parents else {"prices": INPUT_KEY},
        output_schema=(FactorColumnSchema(name, "float64"),),
        row_ordering=("timestamp_ns",),
        research_context={"calendar": "XNYS"},
    )


def _branching_dag(calls: dict[str, int], *, right_version: str = "1"):
    root = _identity("root")
    left = _identity("left", parents=(root.node_key,))
    right = _identity("right", parents=(root.node_key,), version=right_version)
    leaf = _identity("leaf", parents=(left.node_key, right.node_key))

    def compute(name: str, operation):
        def run(parents: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
            calls[name] = calls.get(name, 0) + 1
            return operation(parents)

        return run

    nodes = (
        PolarsFactorNode(root, compute("root", lambda _: pl.DataFrame({"root": [2.0, 4.0, 6.0]}))),
        PolarsFactorNode(
            left,
            compute("left", lambda parents: parents[root.node_key].select((pl.col("root") + 1).alias("left"))),
        ),
        PolarsFactorNode(
            right,
            compute("right", lambda parents: parents[root.node_key].select((pl.col("root") * 2).alias("right"))),
        ),
        PolarsFactorNode(
            leaf,
            compute(
                "leaf",
                lambda parents: pl.DataFrame(
                    {
                        "leaf": parents[left.node_key]["left"] + parents[right.node_key]["right"],
                    }
                ),
            ),
        ),
    )
    return nodes, (root, left, right, leaf)


def test_factor_dag_reuses_all_nodes_on_second_execution(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    calls: dict[str, int] = {}
    nodes, identities = _branching_dag(calls)
    executor = PolarsFactorDagExecutor(tmp_path, FactorCacheAdmissionPolicy.always())

    with executor.execute(nodes) as first:
        assert first.telemetry.nodes_computed == 4
        assert first.telemetry.nodes_reused == 0
        assert first[identities[-1].node_key]["leaf"].to_list() == [7.0, 13.0, 19.0]
    with executor.execute(nodes) as second:
        assert second.telemetry.nodes_computed == 0
        assert second.telemetry.nodes_reused == 4
        assert second[identities[-1].node_key]["leaf"].to_list() == [7.0, 13.0, 19.0]

    assert calls == {"root": 1, "left": 1, "right": 1, "leaf": 1}


def test_factor_dag_partially_invalidates_changed_branch_and_descendants(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    calls: dict[str, int] = {}
    initial_nodes, initial_identities = _branching_dag(calls)
    executor = PolarsFactorDagExecutor(tmp_path, FactorCacheAdmissionPolicy.always())
    with executor.execute(initial_nodes):
        pass
    changed_nodes, changed_identities = _branching_dag(calls, right_version="2")

    with executor.execute(changed_nodes) as changed:
        assert changed.telemetry.cache_hits == (
            initial_identities[0].node_key,
            initial_identities[1].node_key,
        )
        assert changed.telemetry.cache_misses == (
            changed_identities[2].node_key,
            changed_identities[3].node_key,
        )
        assert changed[changed_identities[-1].node_key]["leaf"].to_list() == [7.0, 13.0, 19.0]

    assert calls == {"root": 1, "left": 1, "right": 2, "leaf": 2}


def test_factor_dag_requires_topological_order_and_exact_output_schema(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    calls: dict[str, int] = {}
    nodes, _ = _branching_dag(calls)
    executor = PolarsFactorDagExecutor(tmp_path)

    with pytest.raises(ValueError, match="topologically ordered"):
        executor.execute((nodes[1], nodes[0]))
    invalid = PolarsFactorNode(
        _identity("expected"),
        lambda _: pl.DataFrame({"unexpected": [1.0]}),
    )
    with pytest.raises(ValueError, match="do not match"):
        executor.execute((invalid,))


def test_closed_factor_dag_execution_rejects_access(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    nodes, identities = _branching_dag({})
    execution = PolarsFactorDagExecutor(tmp_path).execute(nodes)

    execution.close()

    with pytest.raises(RuntimeError, match="closed"):
        execution[identities[0].node_key]


def test_cost_aware_policy_reuses_persisted_rejection_hints(tmp_path, monkeypatch) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    calls: dict[str, int] = {}
    nodes, _ = _branching_dag(calls)
    policy = FactorCacheAdmissionPolicy(minimum_expected_uses=3)
    executor = PolarsFactorDagExecutor(tmp_path, policy)

    with executor.execute(nodes) as first:
        assert first.telemetry.cache_writes == ()
        assert first.telemetry.cache_declines == tuple(node.identity.node_key for node in nodes)
        assert len(first.telemetry.compute_measurements) == 4

    def unexpected_cache_probe(*_args, **_kwargs):
        raise AssertionError("persisted rejection should bypass cache lookup")

    monkeypatch.setattr(factor_dag_module, "open_generation_by_node_key", unexpected_cache_probe)
    with executor.execute(nodes) as second:
        assert second.telemetry.nodes_reused == 0
        assert second.telemetry.nodes_computed == 4
        assert second.telemetry.rejection_hints == tuple(node.identity.node_key for node in nodes)

    assert calls == {"root": 2, "left": 2, "right": 2, "leaf": 2}


def test_cost_aware_policy_admits_only_when_estimated_total_cost_improves() -> None:
    policy = FactorCacheAdmissionPolicy(
        estimated_read_bytes_per_second=1_000,
        estimated_write_bytes_per_second=500,
        fixed_read_seconds=0,
        fixed_write_seconds=0,
        minimum_speedup=1.1,
    )

    assert policy.admit(compute_seconds=10.0, output_bytes=1_000, expected_uses=5)
    assert not policy.admit(compute_seconds=0.1, output_bytes=1_000, expected_uses=5)
    assert not policy.admit(compute_seconds=10.0, output_bytes=1_000, expected_uses=1)


def test_factor_cache_policy_validates_configuration_and_measurements() -> None:
    with pytest.raises(ValueError, match="positive"):
        FactorCacheAdmissionPolicy(minimum_expected_uses=0)
    with pytest.raises(ValueError, match="non-negative"):
        FactorCacheAdmissionPolicy().admit(compute_seconds=-1, output_bytes=0, expected_uses=2)
    with pytest.raises(ValueError, match="positive"):
        FactorCacheAdmissionPolicy(estimated_read_bytes_per_second=math.inf)
    with pytest.raises(ValueError, match="non-negative"):
        FactorCacheAdmissionPolicy().admit(compute_seconds=math.nan, output_bytes=0, expected_uses=2)


def test_factor_cache_policy_key_changes_only_with_admission_calibration() -> None:
    first = FactorCacheAdmissionPolicy(rejection_ttl_seconds=60)
    different_ttl = FactorCacheAdmissionPolicy(rejection_ttl_seconds=120)
    different_cost = FactorCacheAdmissionPolicy(fixed_read_seconds=0.1)

    assert first.policy_key == different_ttl.policy_key
    assert first.policy_key != different_cost.policy_key


def test_persisted_rejection_does_not_mask_node_published_by_another_policy(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    calls: dict[str, int] = {}
    nodes, _ = _branching_dag(calls)
    root_node = (nodes[0],)
    rejecting = PolarsFactorDagExecutor(
        tmp_path,
        FactorCacheAdmissionPolicy(minimum_expected_uses=3),
    )
    with rejecting.execute(root_node) as first:
        assert first.telemetry.cache_declines == (root_node[0].identity.node_key,)

    forced = PolarsFactorDagExecutor(tmp_path, FactorCacheAdmissionPolicy.always())
    with forced.execute(root_node) as published:
        assert published.telemetry.cache_writes == (root_node[0].identity.node_key,)

    with rejecting.execute(root_node) as reused:
        assert reused.telemetry.cache_hits == (root_node[0].identity.node_key,)
        assert reused.telemetry.rejection_hints == ()

    assert calls["root"] == 2
