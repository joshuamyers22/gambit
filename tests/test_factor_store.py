from __future__ import annotations

import json
import multiprocessing
import os
import socket
from pathlib import Path
from typing import NoReturn

import numpy as np
import pytest

from gambit.factor_cache import MappedFloat64Column
from gambit.factor_identity import FactorColumnSchema, FactorNodeIdentity
from gambit.factor_store import (
    FactorNodeCacheMiss,
    FactorStoreError,
    collect_garbage,
    evict_factor_nodes,
    open_current_generation,
    open_generation_by_node_key,
    publish_factor_node,
    publish_generation,
)

pytestmark = pytest.mark.native
NODE_A = "a" * 64
NODE_B = "b" * 64


def _node_identity(version: str = "1") -> FactorNodeIdentity:
    return FactorNodeIdentity(
        transform="tests.factor",
        transform_version=version,
        input_fingerprints={"prices": NODE_A},
        parameters={"window": 20},
        output_schema=(FactorColumnSchema("factor", "float64"),),
        row_ordering=("timestamp_ns",),
        research_context={"calendar": "XNYS"},
    )


def _terminate_process() -> NoReturn:
    os._exit(86)


def _publish_until_crash(root: str, stage: str) -> None:
    """Simulate power loss at a publication durability boundary."""
    import gambit.factor_store as factor_store

    if stage == "column":
        original_create = factor_store.MappedFloat64Column.create_chunked

        def create_then_terminate(path, values):
            original_create(path, values)
            _terminate_process()

        factor_store.MappedFloat64Column.create_chunked = create_then_terminate
    elif stage == "manifest":
        original_fsync_directory = factor_store._fsync_directory

        def fsync_then_maybe_terminate(path: Path) -> None:
            original_fsync_directory(path)
            if path.name.startswith(".staging-"):
                _terminate_process()

        factor_store._fsync_directory = fsync_then_maybe_terminate
    elif stage == "generation":
        original_fsync_directory = factor_store._fsync_directory

        def fsync_then_maybe_terminate(path: Path) -> None:
            original_fsync_directory(path)
            if path.name == "generations":
                _terminate_process()

        factor_store._fsync_directory = fsync_then_maybe_terminate
    elif stage == "pointer":
        original_replace = factor_store.os.replace

        def replace_then_terminate(source, destination) -> NoReturn:
            original_replace(source, destination)
            _terminate_process()

        factor_store.os.replace = replace_then_terminate
    else:
        raise AssertionError(f"unknown crash stage: {stage}")

    factor_store.publish_generation(
        root,
        NODE_B,
        {"factor": np.array([2.0, 3.0])},
    )


def _hold_current_lease(root: str, connection) -> None:
    with open_current_generation(root) as lease:
        connection.send((lease.generation, lease["factor"].values.tolist()))
        connection.recv()
    connection.close()


def _hold_writer_lock(root: str, connection) -> None:
    import gambit.factor_store as factor_store

    with factor_store._writer_lock(Path(root)):
        connection.send("locked")
        connection.recv()
    connection.close()


def _collect_and_report(root: str, connection) -> None:
    connection.send(collect_garbage(root))
    connection.close()


def _publish_factor_and_report(root: str, connection) -> None:
    generation = publish_factor_node(root, _node_identity(), {"factor": np.array([1.0, 2.0])})
    connection.send(generation)
    connection.close()


def test_factor_store_atomically_publishes_generations(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    first = publish_generation(tmp_path, NODE_A, {"alpha": np.array([1.0, 2.0])})
    second = publish_generation(tmp_path, NODE_B, {"alpha": np.array([3.0, 4.0])})

    current = open_current_generation(tmp_path)

    assert first != second
    assert np.array_equal(current["alpha"].values, np.array([3.0, 4.0]))
    assert (tmp_path / "generations" / first / "manifest.json").is_file()
    assert (tmp_path / "CURRENT").read_text() == f"{second}\n"


def test_factor_store_persists_and_opens_strict_identity_by_node_key(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    identity = _node_identity()
    generation = publish_factor_node(tmp_path, identity, {"factor": np.array([1.0, 2.0])})

    with open_generation_by_node_key(tmp_path, identity.node_key) as cached:
        assert cached.generation == generation
        assert cached.node_key == identity.node_key
        assert cached.identity is not None
        assert cached.identity.snapshot() == identity.snapshot()
        assert np.array_equal(cached["factor"].values, np.array([1.0, 2.0]))
    manifest = json.loads((tmp_path / "generations" / generation / "manifest.json").read_text())
    assert manifest["identity"] == identity.snapshot()


def test_factor_store_rate_limits_persisted_access_metadata(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    identity = _node_identity()
    generation = publish_factor_node(tmp_path, identity, {"factor": np.array([1.0])})
    access_path = tmp_path / "access" / f"{identity.node_key}.json"
    initial = json.loads(access_path.read_text())
    assert initial["generation"] == generation
    assert initial["access_count"] == 1

    with open_generation_by_node_key(tmp_path, identity.node_key):
        pass
    assert json.loads(access_path.read_text())["access_count"] == 1

    with open_generation_by_node_key(
        tmp_path,
        identity.node_key,
        access_update_interval_seconds=0,
    ):
        pass
    assert json.loads(access_path.read_text())["access_count"] == 2


def test_factor_store_reuses_valid_generation_for_same_node_key(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    identity = _node_identity()
    first = publish_factor_node(tmp_path, identity, {"factor": np.array([1.0, 2.0])})
    reused = publish_factor_node(tmp_path, identity, {"factor": np.array([99.0, 100.0])})

    assert reused == first
    assert len(list((tmp_path / "generations").glob("[0-9a-f]" * 32))) == 1
    with open_generation_by_node_key(tmp_path, identity.node_key) as cached:
        assert np.array_equal(cached["factor"].values, np.array([1.0, 2.0]))


def test_factor_store_same_node_publication_is_deduplicated_across_processes(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    context = multiprocessing.get_context("spawn")
    connections = [context.Pipe() for _ in range(2)]
    processes = [
        context.Process(target=_publish_factor_and_report, args=(str(tmp_path), child))
        for _, child in connections
    ]
    for (parent, child), process in zip(connections, processes):
        process.start()
        child.close()
    generations = [parent.recv() for parent, _ in connections]
    for (parent, _), process in zip(connections, processes):
        parent.close()
        process.join(timeout=30)
        assert process.exitcode == 0

    assert generations[0] == generations[1]
    assert len(list((tmp_path / "generations").glob("[0-9a-f]" * 32))) == 1


def test_factor_store_node_lookup_miss_and_identity_tampering_fail_closed(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    with pytest.raises(FactorStoreError, match="not cached"):
        open_generation_by_node_key(tmp_path, NODE_A)

    identity = _node_identity()
    generation = publish_factor_node(tmp_path, identity, {"factor": np.array([1.0])})
    manifest_path = tmp_path / "generations" / generation / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["identity"]["parameters"]["window"] = 21
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(FactorStoreError, match="hash mismatch"):
        open_generation_by_node_key(tmp_path, identity.node_key)


def test_factor_store_rejects_schema_that_storage_cannot_honor(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    wrong_name = _node_identity()
    with pytest.raises(ValueError, match="exactly match"):
        publish_factor_node(tmp_path, wrong_name, {"other": np.array([1.0])})
    nullable = FactorNodeIdentity(
        transform="tests.factor",
        transform_version="1",
        input_fingerprints={"prices": NODE_A},
        output_schema=(FactorColumnSchema("factor", "float64", nullable=True),),
        row_ordering=("timestamp_ns",),
    )
    with pytest.raises(ValueError, match="non-nullable float64"):
        publish_factor_node(tmp_path, nullable, {"factor": np.array([1.0])})


def test_factor_store_ignores_orphaned_staging_directory(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    orphan = tmp_path / "generations" / ".staging-deadbeef"
    orphan.mkdir()
    (orphan / "manifest.json").write_text("{}")

    assert open_current_generation(tmp_path)["factor"].values[0] == 1.0


def test_factor_store_collects_orphaned_publication_artifacts(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    staging = tmp_path / "generations" / f".staging-{'c' * 32}"
    staging.mkdir()
    (staging / "partial.bin").write_bytes(b"partial")
    pointer = tmp_path / f".CURRENT-{'d' * 32}"
    pointer.write_text(f"{'d' * 32}\n")

    result = collect_garbage(tmp_path)

    assert result["removed_staging"] == [staging.name]
    assert result["removed_pointers"] == [pointer.name]
    assert not staging.exists()
    assert not pointer.exists()


def test_factor_store_does_not_collect_while_writer_is_active(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    context = multiprocessing.get_context("spawn")
    parent_writer, child_writer = context.Pipe()
    parent_collector, child_collector = context.Pipe()
    writer = context.Process(target=_hold_writer_lock, args=(str(tmp_path), child_writer))
    collector = context.Process(
        target=_collect_and_report,
        args=(str(tmp_path), child_collector),
    )
    writer.start()
    child_writer.close()
    assert parent_writer.recv() == "locked"
    collector.start()
    child_collector.close()
    try:
        assert not parent_collector.poll(0.25)
        parent_writer.send("release")
        result = parent_collector.recv()
        assert result["removed_generations"] == []
    finally:
        parent_writer.close()
        parent_collector.close()
        writer.join(timeout=30)
        collector.join(timeout=30)
        for process in (writer, collector):
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)

    assert writer.exitcode == 0
    assert collector.exitcode == 0


@pytest.mark.parametrize("stage", ["column", "manifest", "generation", "pointer"])
def test_factor_store_recovers_from_process_death_during_publication(tmp_path, stage) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    first = publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_publish_until_crash, args=(str(tmp_path), stage))

    process.start()
    process.join(timeout=30)

    assert process.exitcode == 86
    with open_current_generation(tmp_path) as current:
        values = current["factor"].values.copy()
        if stage == "pointer":
            assert current.generation != first
            assert np.array_equal(values, np.array([2.0, 3.0]))
        else:
            assert current.generation == first
            assert np.array_equal(values, np.array([1.0]))


def test_factor_store_rejects_pointer_and_manifest_substitution(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    generation = publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    (tmp_path / "CURRENT").write_text("../outside\n")
    with pytest.raises(FactorStoreError, match="CURRENT generation is invalid"):
        open_current_generation(tmp_path)

    (tmp_path / "CURRENT").write_text(f"{generation}\n")
    manifest_path = tmp_path / "generations" / generation / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["columns"]["factor"]["file"] = "../outside.bin"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(FactorStoreError, match="filename is invalid"):
        open_current_generation(tmp_path)


def test_factor_store_rejects_empty_and_unsafe_publication(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    with pytest.raises(ValueError, match="must not be empty"):
        publish_generation(tmp_path, NODE_A, {})
    with pytest.raises(ValueError, match="SHA-256"):
        publish_generation(tmp_path, "node-a", {"factor": np.array([1.0])})
    with pytest.raises(ValueError, match="safe portable"):
        publish_generation(tmp_path, NODE_A, {"../factor": np.array([1.0])})
    with pytest.raises(ValueError, match="one-dimensional"):
        publish_generation(tmp_path, NODE_A, {"factor": np.ones((2, 2))})
    with pytest.raises(ValueError, match="equal row counts"):
        publish_generation(
            tmp_path,
            NODE_A,
            {"alpha": np.array([1.0]), "beta": np.array([1.0, 2.0])},
        )


def test_factor_store_rejects_symlinked_column(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    generation = publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    column_path = tmp_path / "generations" / generation / "factor.bin"
    outside = tmp_path / "outside.bin"
    column_path.rename(outside)
    column_path.symlink_to(outside)

    with pytest.raises(FactorStoreError, match="symbolic links"):
        open_current_generation(tmp_path)


def test_factor_store_garbage_collection_respects_active_lease(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    first = publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    lease = open_current_generation(tmp_path)
    publish_generation(tmp_path, NODE_B, {"factor": np.array([2.0])})

    while_leased = collect_garbage(tmp_path)
    assert while_leased["removed_generations"] == []
    assert (tmp_path / "generations" / first).is_dir()

    lease.close()
    after_close = collect_garbage(tmp_path)
    assert after_close["removed_generations"] == [first]
    assert not (tmp_path / "generations" / first).exists()


def test_factor_store_garbage_collection_preserves_indexed_nodes(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    first_identity = _node_identity("1")
    second_identity = _node_identity("2")
    first = publish_factor_node(tmp_path, first_identity, {"factor": np.array([1.0])})
    publish_factor_node(tmp_path, second_identity, {"factor": np.array([2.0])})

    result = collect_garbage(tmp_path)

    assert result["removed_generations"] == []
    assert (tmp_path / "generations" / first).is_dir()
    with open_generation_by_node_key(tmp_path, first_identity.node_key) as cached:
        assert cached["factor"].values[0] == 1.0


def test_factor_store_evicts_least_recently_used_unleased_node(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    identities = [_node_identity(version) for version in ("1", "2", "3")]
    generations = [
        publish_factor_node(tmp_path, identity, {"factor": np.array([float(index)])})
        for index, identity in enumerate(identities)
    ]
    for last_access_ns, identity in enumerate(identities, start=1):
        access_path = tmp_path / "access" / f"{identity.node_key}.json"
        access = json.loads(access_path.read_text())
        access["last_access_ns"] = last_access_ns
        access_path.write_text(json.dumps(access))

    preview = evict_factor_nodes(tmp_path, max_bytes=10**9, max_nodes=2, dry_run=True)

    assert preview["dry_run"] is True
    assert preview["evicted_node_keys"] == [identities[0].node_key]
    assert (tmp_path / "generations" / generations[0]).is_dir()
    assert (tmp_path / "nodes" / identities[0].node_key).is_file()

    result = evict_factor_nodes(tmp_path, max_bytes=10**9, max_nodes=2)

    assert result["evicted_node_keys"] == [identities[0].node_key]
    assert result["removed_generations"] == [generations[0]]
    assert result["total_nodes_before"] == 3
    assert result["total_nodes_after"] == 2
    assert result["limits_satisfied"] is True
    with pytest.raises(FactorNodeCacheMiss):
        open_generation_by_node_key(tmp_path, identities[0].node_key)
    with open_generation_by_node_key(tmp_path, identities[1].node_key):
        pass


def test_factor_store_garbage_collection_dry_run_is_non_mutating(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    staging = tmp_path / "generations" / ".staging-preview"
    staging.mkdir()

    preview = collect_garbage(tmp_path, dry_run=True)

    assert preview["dry_run"] is True
    assert preview["removed_staging"] == [staging.name]
    assert staging.is_dir()


def test_factor_store_collects_only_old_orphan_metadata(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    live_identity = _node_identity("live")
    publish_factor_node(tmp_path, live_identity, {"factor": np.array([1.0])})
    orphan_key = "f" * 64
    old_paths = []
    for directory_name in ("access", "admission"):
        directory = tmp_path / directory_name
        directory.mkdir(exist_ok=True)
        orphan = directory / f"{orphan_key}.json"
        orphan.write_text("{}")
        os.utime(orphan, ns=(0, 0))
        old_paths.append(orphan)
    recent_orphan = tmp_path / "admission" / f"{'e' * 64}.json"
    recent_orphan.write_text("{}")
    live_access = tmp_path / "access" / f"{live_identity.node_key}.json"
    os.utime(live_access, ns=(0, 0))

    preview = collect_garbage(tmp_path, metadata_retention_seconds=1, dry_run=True)

    assert preview["removed_metadata"] == [
        f"access/{orphan_key}.json",
        f"admission/{orphan_key}.json",
    ]
    assert all(path.is_file() for path in old_paths)

    applied = collect_garbage(tmp_path, metadata_retention_seconds=1)

    assert applied["removed_metadata"] == preview["removed_metadata"]
    assert all(not path.exists() for path in old_paths)
    assert recent_orphan.is_file()
    assert live_access.is_file()


def test_factor_store_eviction_protects_current_and_leased_generations(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    first_identity = _node_identity("1")
    current_identity = _node_identity("2")
    first = publish_factor_node(tmp_path, first_identity, {"factor": np.array([1.0])})
    publish_factor_node(tmp_path, current_identity, {"factor": np.array([2.0])})
    lease = open_generation_by_node_key(tmp_path, first_identity.node_key)

    protected = evict_factor_nodes(tmp_path, max_bytes=0, max_nodes=0)

    assert set(protected["protected_node_keys"]) == {
        first_identity.node_key,
        current_identity.node_key,
    }
    assert protected["evicted_node_keys"] == []
    assert protected["limits_satisfied"] is False
    assert (tmp_path / "generations" / first).is_dir()

    lease.close()
    after_close = evict_factor_nodes(tmp_path, max_bytes=10**9, max_nodes=1)
    assert after_close["evicted_node_keys"] == [first_identity.node_key]
    assert after_close["limits_satisfied"] is True


def test_factor_store_eviction_enforces_actual_generation_byte_limit(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    large_identity = _node_identity("large")
    current_identity = _node_identity("current")
    publish_factor_node(tmp_path, large_identity, {"factor": np.arange(10_000, dtype=np.float64)})
    current = publish_factor_node(tmp_path, current_identity, {"factor": np.array([1.0])})
    current_path = tmp_path / "generations" / current
    current_bytes = sum(path.stat().st_size for path in current_path.iterdir() if path.is_file())

    result = evict_factor_nodes(tmp_path, max_bytes=current_bytes)

    assert result["evicted_node_keys"] == [large_identity.node_key]
    assert result["total_bytes_after"] == current_bytes
    assert result["limits_satisfied"] is True


def test_factor_store_eviction_and_access_limits_validate_inputs(tmp_path) -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        evict_factor_nodes(tmp_path, max_bytes=-1)
    with pytest.raises(ValueError, match="max_nodes"):
        evict_factor_nodes(tmp_path, max_bytes=0, max_nodes=-1)
    with pytest.raises(ValueError, match="access_update"):
        open_generation_by_node_key(tmp_path, NODE_A, access_update_interval_seconds=-1)
    with pytest.raises(ValueError, match="metadata_retention_seconds"):
        collect_garbage(tmp_path, metadata_retention_seconds=float("inf"))


def test_factor_store_garbage_collection_respects_cross_process_lease(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    first = publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe()
    reader = context.Process(
        target=_hold_current_lease,
        args=(str(tmp_path), child_connection),
    )
    reader.start()
    child_connection.close()
    try:
        generation, values = parent_connection.recv()
        assert generation == first
        assert values == [1.0]
        publish_generation(tmp_path, NODE_B, {"factor": np.array([2.0])})

        result = collect_garbage(tmp_path)

        assert result["removed_generations"] == []
        assert (tmp_path / "generations" / first).is_dir()
    finally:
        parent_connection.send("close")
        parent_connection.close()
        reader.join(timeout=30)
        if reader.is_alive():
            reader.terminate()
            reader.join(timeout=5)

    assert reader.exitcode == 0
    result = collect_garbage(tmp_path)
    assert result["removed_generations"] == [first]


def test_closed_factor_store_lease_rejects_access(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    lease = open_current_generation(tmp_path)

    lease.close()

    with pytest.raises(FactorStoreError, match="lease is closed"):
        lease["factor"]
    lease.close()


def test_factor_store_collects_old_dead_local_lease(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    first = publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    publish_generation(tmp_path, NODE_B, {"factor": np.array([2.0])})
    lease_directory = tmp_path / "leases" / first
    lease_directory.mkdir(parents=True)
    stale = lease_directory / "99999999-dead.json"
    stale.write_text(
        json.dumps(
            {
                "generation": first,
                "pid": 99999999,
                "host": socket.gethostname(),
                "created_ns": 0,
            }
        )
    )

    result = collect_garbage(tmp_path, stale_lease_seconds=0)

    assert result["removed_generations"] == [first]
    assert result["removed_leases"] == [f"{first}/{stale.name}"]
