from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_benchmark_module():
    path = Path(__file__).parents[1] / "benchmarks" / "factor_metrics_benchmark.py"
    spec = importlib.util.spec_from_file_location("factor_metrics_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_factor_metrics_benchmark_smoke(tmp_path) -> None:
    benchmark = _load_benchmark_module()
    result = benchmark.run_benchmark(tmp_path, samples=3)

    assert [entry["operation"] for entry in result["measurements"]] == [
        "atomic_record",
        "locked_read",
        "openmetrics_render",
    ]
    assert all(entry["samples"] == 3 for entry in result["measurements"])
    assert result["final_counters"]["cache_hits"] == 3
