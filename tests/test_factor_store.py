from __future__ import annotations

import json
import socket

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
