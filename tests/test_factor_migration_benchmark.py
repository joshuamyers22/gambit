from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from gambit.factor_cache import MappedFloat64Column


def _load_benchmark_module():
    path = Path(__file__).parents[1] / "benchmarks" / "factor_migration_benchmark.py"
    spec = importlib.util.spec_from_file_location("factor_migration_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.native
def test_factor_migration_benchmark_smoke(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    benchmark = _load_benchmark_module()

    result = benchmark.run_benchmark(1_000, 2, tmp_path)

    assert result["migration"]["exact_equality"] is True
    assert result["migration"]["segment_versions"] == [3]
    assert result["migration"]["host_allocation_write_amplification"] > 0
    assert result["allocation"]["peak_bytes"] > result["allocation"]["before_bytes"]
    assert result["collection"]["removed_generations"] == [result["migration"]["old_generation"]]
