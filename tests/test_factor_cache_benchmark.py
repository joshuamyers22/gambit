import importlib.util
import sys
from pathlib import Path


def _load_benchmark_module():
    path = Path(__file__).parents[1] / "benchmarks" / "factor_cache_benchmark.py"
    spec = importlib.util.spec_from_file_location("factor_cache_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_factor_cache_benchmark_smoke(tmp_path) -> None:
    benchmark = _load_benchmark_module()

    result = benchmark.run_benchmark(1_000, 1, tmp_path)

    names = {measurement["name"] for measurement in result["measurements"]}
    assert "polars_factor_dag" in names
    assert "polars_ipc_mmap_read" in names
    assert "polars_parquet_read" in names
    assert "numpy_raw_mmap_reopen_read" in names
    if benchmark.MappedFloat64Column is not None:
        assert "native_chunked_mmap_reopen_prefix_read" in names
    assert result["workload"]["factor_columns"] == 7
    assert all(value is not False for value in result["equality"].values())
    for artifact in result["artifacts"].values():
        assert artifact["files"] > 0
        assert artifact["stored_bytes"] > 0
        assert artifact["allocated_bytes"] > 0
        assert artifact["file_size_amplification"] > 0
        assert artifact["filesystem_allocation_amplification"] > 0
    assert result["decision_metrics"]["ipc_reuse_speedup_vs_recompute"] > 0
    assert result["environment"]["device_level_write_amplification_measured"] is False
