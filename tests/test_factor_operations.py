from __future__ import annotations

import numpy as np
import pytest

from gambit.factor_cache import MappedFloat64Column
from gambit.factor_identity import FactorColumnSchema, FactorNodeIdentity
from gambit.factor_operations import calibrate_factor_cache, inspect_factor_cache
from gambit.factor_store import open_generation_by_node_key, publish_factor_node, publish_generation

pytestmark = pytest.mark.native
INPUT_KEY = "a" * 64


def _identity(version: str = "1") -> FactorNodeIdentity:
    return FactorNodeIdentity(
        transform="tests.inventory",
        transform_version=version,
        input_fingerprints={"prices": INPUT_KEY},
        output_schema=(FactorColumnSchema("factor", "float64"),),
        row_ordering=("timestamp_ns",),
    )


def test_factor_cache_inventory_reports_indexed_storage_and_live_lease(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    identity = _identity()
    generation = publish_factor_node(tmp_path, identity, {"factor": np.array([1.0, 2.0])})
    lease = open_generation_by_node_key(tmp_path, identity.node_key)

    inventory = inspect_factor_cache(tmp_path)

    assert inventory.generation_count == 1
    assert inventory.unindexed_generation_count == 0
    assert inventory.lease_file_count == 1
    assert inventory.access_record_count == 1
    assert inventory.rejection_hint_count == 0
    assert inventory.total_cache_file_bytes >= inventory.indexed_generation_bytes > 0
    assert inventory.total_cache_allocated_bytes >= inventory.total_cache_file_bytes
    assert inventory.device_wear_measured is False
    assert inventory.findings == ()
    assert len(inventory.indexed_nodes) == 1
    node = inventory.indexed_nodes[0]
    assert node.node_key == identity.node_key
    assert node.generation == generation
    assert node.column_count == 1
    assert node.row_count == 2
    assert node.lease_count == 1
    assert node.current is True
    assert inventory.snapshot()["indexed_nodes"][0]["node_key"] == identity.node_key
    lease.close()


def test_factor_cache_inventory_reports_unindexed_and_malformed_state(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    publish_generation(tmp_path, "b" * 64, {"factor": np.array([1.0])})
    nodes = tmp_path / "nodes"
    nodes.mkdir()
    (nodes / "invalid-key").write_text("missing")

    inventory = inspect_factor_cache(tmp_path)

    assert inventory.generation_count == 1
    assert inventory.unindexed_generation_count == 1
    assert [finding.code for finding in inventory.findings] == ["invalid-node-index"]


def test_factor_cache_calibration_measures_selected_filesystem_and_cleans_up(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")

    calibration = calibrate_factor_cache(
        tmp_path,
        sample_bytes=(64 * 1024, 256 * 1024),
        repeats=1,
        request_page_cache_eviction=False,
    )

    assert calibration.device_id == tmp_path.stat().st_dev
    assert calibration.estimated_read_bytes_per_second > 0
    assert calibration.estimated_write_bytes_per_second > 0
    assert calibration.fixed_read_seconds >= 0
    assert calibration.fixed_write_seconds >= 0
    assert calibration.page_cache_eviction_requested is False
    assert calibration.device_wear_measured is False
    assert calibration.admission_policy().estimated_read_bytes_per_second == (
        calibration.estimated_read_bytes_per_second
    )
    assert not list(tmp_path.glob(".calibration-*"))


def test_factor_cache_operations_validate_roots_and_calibration_shape(tmp_path) -> None:
    with pytest.raises(ValueError, match="existing"):
        inspect_factor_cache(tmp_path / "missing")
    with pytest.raises(ValueError, match="two increasing"):
        calibrate_factor_cache(tmp_path, sample_bytes=(4096, 4096), repeats=1)
    with pytest.raises(ValueError, match="positive"):
        calibrate_factor_cache(tmp_path, sample_bytes=(4096, 8192), repeats=0)
