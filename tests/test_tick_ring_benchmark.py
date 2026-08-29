import importlib.util
import sys
from pathlib import Path

import pytest


def _load_benchmark_module():
    path = Path(__file__).parents[1] / "benchmarks" / "tick_ring_benchmark.py"
    spec = importlib.util.spec_from_file_location("tick_ring_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tick_ring_benchmark_smoke() -> None:
    benchmark = _load_benchmark_module()

    result = benchmark.run_benchmark(1_000, 64, 256)

    measurements = {measurement["name"]: measurement for measurement in result["measurements"]}
    assert measurements["python_queue_per_tick"]["sequence_errors"] == 0
    assert measurements["python_queue_batch"]["sequence_errors"] == 0
    if "native_spsc_batch" in measurements:
        assert measurements["native_spsc_batch"]["sequence_errors"] == 0
        assert measurements["native_spsc_batch"]["rejected_pushes"] == 0
    if "native_spsc_in_place_factors" in measurements:
        assert measurements["native_spsc_in_place_factors"]["sequence_errors"] == 0
        assert measurements["native_spsc_in_place_factors"]["rejected_pushes"] == 0


def test_tick_ring_benchmark_matrix_smoke() -> None:
    benchmark = _load_benchmark_module()

    result = benchmark.run_matrix(
        1_000,
        [64],
        [256],
        [0, 64],
        [0.0, 0.001],
        [0, 2],
        0.0001,
        repeats=2,
        warmups=0,
    )

    assert result["workload"] == {"ticks": 1_000, "repeats": 2, "warmups": 0}
    assert len(result["configurations"]) == 8
    for configuration in result["configurations"]:
        assert configuration["batch_size"] == 64
        assert configuration["capacity"] == 256
        assert configuration["park_timeout_seconds"] in {0.0, 0.001}
        assert configuration["backoff_count"] in {0, 2}
        for measurement in configuration["measurements"]:
            assert measurement["median_ticks_per_second"] > 0
            assert measurement["p50_trial_latency_seconds"] > 0
            assert measurement["p99_trial_latency_seconds"] > 0
            assert measurement["median_spins"] >= 0
            assert measurement["median_parks"] >= 0
            assert measurement["median_backoffs"] >= 0
            assert measurement["sequence_errors"] == 0
            assert measurement["rejected_pushes"] == 0


def test_tick_ring_benchmark_matrix_rejects_invalid_dimensions() -> None:
    benchmark = _load_benchmark_module()

    with pytest.raises(ValueError, match="must not be empty"):
        benchmark.run_matrix(1_000, [], [256], [64], [0.001], [0], 0.001, repeats=1, warmups=0)
    with pytest.raises(ValueError, match="power of two"):
        benchmark.run_matrix(1_000, [64], [250], [64], [0.001], [0], 0.001, repeats=1, warmups=0)
    with pytest.raises(ValueError, match="park timeouts"):
        benchmark.run_matrix(1_000, [64], [256], [64], [-1.0], [0], 0.001, repeats=1, warmups=0)
