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
from gambit.factor_store import (
    FactorStoreError,
    collect_garbage,
    open_current_generation,
    publish_generation,
)

pytestmark = pytest.mark.native
NODE_A = "a" * 64
NODE_B = "b" * 64


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


def test_factor_store_ignores_orphaned_staging_directory(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    publish_generation(tmp_path, NODE_A, {"factor": np.array([1.0])})
    orphan = tmp_path / "generations" / ".staging-deadbeef"
    orphan.mkdir()
    (orphan / "manifest.json").write_text("{}")

    assert open_current_generation(tmp_path)["factor"].values[0] == 1.0


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
