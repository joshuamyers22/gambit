from __future__ import annotations

from collections.abc import Mapping

import polars as pl
import pytest

from gambit.factor_cache import MappedFloat64Column
from gambit.factor_dag import PolarsFactorDagExecutor, PolarsFactorNode
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
    executor = PolarsFactorDagExecutor(tmp_path)

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
    executor = PolarsFactorDagExecutor(tmp_path)
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
