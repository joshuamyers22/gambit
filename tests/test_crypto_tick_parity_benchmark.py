import importlib.util
from pathlib import Path

import numpy as np
import pytest

from gambit.factor_cache import TickFactorProcessor, TickRing


def _load_module(filename):
    spec = importlib.util.spec_from_file_location(filename.stem, filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def benchmark():
    return _load_module(Path(__file__).parents[1] / "benchmarks" / "crypto_tick_parity.py")


def test_crypto_stream_is_chunk_invariant_and_seeded(benchmark):
    full = benchmark.make_ticks(0, 1003, 10, 42)
    split = np.concatenate([benchmark.make_ticks(0, 411, 10, 42), benchmark.make_ticks(411, 592, 10, 42)])
    assert full.tobytes() == split.tobytes()
    assert full.tobytes() != benchmark.make_ticks(0, 1003, 10, 43).tobytes()
    assert np.all(np.diff(full["event_time_ns"]) > 0)
    assert np.all(full["receive_time_ns"] >= full["event_time_ns"])
    assert len(np.unique(full["instrument_id"])) == 8


def test_streaming_python_oracle_matches_existing_reference_with_sequence_gaps_and_zero_prices(benchmark):
    original = _load_module(Path(__file__).with_name("test_native_reference.py"))
    records = benchmark.make_ticks(0, 1003, 10, 42)
    records["sequence"][::73] += 1
    records["price"][::17] = 0
    records["quantity"][::19] = 0
    records["receive_time_ns"][::7] = records["event_time_ns"][::7] - 1
    processor = benchmark.PythonTickProcessor()
    for chunk in np.array_split(records, 13):
        processor.process(chunk)
    benchmark.compare(processor.snapshot, original._tick_reference(records))
    if TickRing is not None:
        ring, native = TickRing(1024), TickFactorProcessor()
        assert ring.push_batch(records) == len(records)
        while ring.depth:
            ring.process_batch(native, 31)
        benchmark.compare(native.snapshot, processor.snapshot)


@pytest.mark.parametrize("field,value", [("processed", -1), ("mean_mid", 0.0), ("total_quantity", float("nan"))])
def test_parity_comparison_detects_mismatches(benchmark, field, value):
    processor = benchmark.PythonTickProcessor()
    processor.process(benchmark.make_ticks(0, 10, 10, 42))
    actual = processor.snapshot
    actual[field] = value
    with pytest.raises(AssertionError):
        benchmark.compare(actual, processor.snapshot)


def test_crypto_replay_verifies_all_chunks_and_exact_checkpoints(benchmark):
    if TickRing is None:
        pytest.skip("compiled native extension required")
    result = benchmark.run_replay(1003, chunk_size=61, capacity=64, consume_size=17, checkpoints=(501,))
    assert result["status"] == "pass"
    assert [point["ticks"] for point in result["checkpoints"]] == [501, 1003]
    assert result["ring_metrics"]["popped"] == 1003
    assert result["ring_metrics"]["dropped"] == 0
    assert result["chunks_verified"] == 18
    other = benchmark.run_replay(1003, chunk_size=37, capacity=64, consume_size=11)
    assert result["input_sha256"] == other["input_sha256"]
    assert result["checkpoints"][-1]["native"] == other["checkpoints"][-1]["native"]


@pytest.mark.parametrize("kwargs", [{"ticks": 0}, {"ticks": 10, "rate": 3}, {"ticks": 10, "capacity": 63},
                                    {"ticks": 10, "consume_size": 0}, {"ticks": 10, "seed": -1}])
def test_crypto_replay_rejects_invalid_configuration(benchmark, kwargs):
    if TickRing is None:
        pytest.skip("compiled native extension required")
    with pytest.raises(ValueError):
        benchmark.run_replay(**kwargs)
