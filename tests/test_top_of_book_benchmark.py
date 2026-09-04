import importlib.util
import sys
from pathlib import Path

import pytest

from gambit.tick_backtest import TopOfBookBacktester


def load_benchmark():
    directory = Path(__file__).parents[1] / "benchmarks"
    loaded = None
    for name in ("crypto_tick_parity", "top_of_book_backtest"):
        spec = importlib.util.spec_from_file_location(name, directory / f"{name}.py")
        assert spec is not None and spec.loader is not None
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[name] = loaded
        spec.loader.exec_module(loaded)
    return loaded


def test_book_benchmark_is_chunk_invariant_and_executes_real_orders():
    if TopOfBookBacktester is None:
        pytest.skip("native extension required")
    benchmark = load_benchmark()
    first = benchmark.run(100003, chunk_size=4093)
    second = benchmark.run(100003, chunk_size=8192)
    assert first["input_sha256"] == second["input_sha256"]
    assert first["result_sha256"] == second["result_sha256"]
    assert first["order_count"] > 0
    assert first["fill_count"] > first["order_count"]
    assert first["portfolio"]["processed"] == 100003
    assert first["timing"]["execution_seconds"] > 0


def test_book_benchmark_ledger_check_rejects_corrupt_cash():
    if TopOfBookBacktester is None:
        pytest.skip("native extension required")
    benchmark = load_benchmark()
    engine = TopOfBookBacktester(8, 100000, 1, 10)
    engine.process_batch(benchmark.make_books(0, 100))
    result = engine.result()
    result["cash"] += 1
    with pytest.raises(AssertionError, match="reconcile"):
        benchmark.reconcile(result, 100000)


@pytest.mark.parametrize("interval", [16, 10000])
def test_fifo_benchmark_is_chunk_invariant(interval):
    if TopOfBookBacktester is None:
        pytest.skip("native extension required")
    benchmark = load_benchmark()
    first = benchmark.run(100003, chunk_size=4093, execution_model="fifo", rebalance_events=interval)
    second = benchmark.run(100003, chunk_size=8192, execution_model="fifo", rebalance_events=interval)
    assert first["input_sha256"] == second["input_sha256"]
    assert first["result_sha256"] == second["result_sha256"]
    assert first["fill_count"] > 0
    if interval == 16:
        assert first["order_status_counts"]["2"] > 0
