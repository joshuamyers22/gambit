import importlib.util
import sys
from pathlib import Path


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
