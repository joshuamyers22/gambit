from __future__ import annotations

import json

import numpy as np
import pytest

from gambit.factor_cache import MappedFloat64Column
from gambit.factor_cli import main
from gambit.factor_identity import FactorColumnSchema, FactorNodeIdentity
from gambit.factor_store import publish_factor_node

pytestmark = pytest.mark.native
INPUT_KEY = "a" * 64


def _identity(version: str) -> FactorNodeIdentity:
    return FactorNodeIdentity(
        transform="tests.cli",
        transform_version=version,
        input_fingerprints={"prices": INPUT_KEY},
        output_schema=(FactorColumnSchema("factor", "float64"),),
        row_ordering=("timestamp_ns",),
    )


def test_factor_cache_cli_inventory_emits_json(tmp_path, capsys) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    publish_factor_node(tmp_path, _identity("1"), {"factor": np.array([1.0])})

    assert main(["inventory", str(tmp_path)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert len(result["indexed_nodes"]) == 1
    assert result["device_wear_measured"] is False


def test_factor_cache_cli_eviction_defaults_to_dry_run_and_requires_apply(tmp_path, capsys) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    first = _identity("1")
    second = _identity("2")
    publish_factor_node(tmp_path, first, {"factor": np.array([1.0])})
    publish_factor_node(tmp_path, second, {"factor": np.array([2.0])})

    main(["evict", str(tmp_path), "--max-bytes", "1GiB", "--max-nodes", "1"])
    preview = json.loads(capsys.readouterr().out)
    assert preview["dry_run"] is True
    assert preview["evicted_node_keys"] == [first.node_key]
    assert (tmp_path / "nodes" / first.node_key).is_file()

    main(["evict", str(tmp_path), "--max-bytes", "1GiB", "--max-nodes", "1", "--apply"])
    applied = json.loads(capsys.readouterr().out)
    assert applied["dry_run"] is False
    assert not (tmp_path / "nodes" / first.node_key).exists()


def test_factor_cache_cli_collection_preview_and_output_file(tmp_path, capsys) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    publish_factor_node(tmp_path, _identity("1"), {"factor": np.array([1.0])})
    staging = tmp_path / "generations" / ".staging-cli"
    staging.mkdir()
    output = tmp_path / "collection.json"

    main(
        [
            "collect",
            str(tmp_path),
            "--metadata-retention-seconds",
            "3600",
            "--output",
            str(output),
        ]
    )

    assert capsys.readouterr().out == ""
    preview = json.loads(output.read_text())
    assert preview["dry_run"] is True
    assert preview["removed_staging"] == [staging.name]
    assert preview["removed_metadata"] == []
    assert staging.is_dir()


def test_factor_cache_cli_calibration_accepts_human_sizes(tmp_path, capsys) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")

    main(
        [
            "calibrate",
            str(tmp_path),
            "--small-sample",
            "64KiB",
            "--large-sample",
            "256KiB",
            "--repeats",
            "1",
            "--no-page-cache-eviction",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert result["sample_bytes"] == [65536, 262144]
    assert result["page_cache_eviction_requested"] is False


def test_factor_cache_cli_reports_operational_errors_as_json(tmp_path, capsys) -> None:
    status = main(["inventory", str(tmp_path / "missing")])

    assert status == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error"] == "ValueError"
