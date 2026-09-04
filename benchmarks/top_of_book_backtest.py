"""Measured experimental order-to-P&L replay; generation is timed separately."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import sys
import time
from pathlib import Path

import numpy as np
from crypto_tick_parity import make_ticks

from gambit.tick_backtest import BOOK_DTYPE, TopOfBookBacktester


def make_books(offset, count, rate=10, seed=20260904):
    ticks = make_ticks(offset, count, rate, seed)
    books = np.zeros(count, dtype=BOOK_DTYPE)
    for name in ("sequence", "event_time_ns", "receive_time_ns", "instrument_id"):
        books[name] = ticks[name]
    # Synthetic common quote currency: price tick=0.0001, lot=0.001,
    # so one cash/fee/P&L unit is 0.0000001 quote currency.
    books["bid"] = np.rint(ticks["bid"] * 10000).astype(np.int64)
    books["ask"] = np.rint(ticks["ask"] * 10000).astype(np.int64)
    books["bid_size"] = 20 + (ticks["sequence"] * 17 % 281).astype(np.int64)
    books["ask_size"] = 20 + (ticks["sequence"] * 23 % 281).astype(np.int64)
    return books


def reconcile(result, initial_cash):
    cash = initial_cash
    positions = [0] * len(result["positions"])
    fees = 0
    for fill in result["fills"]:
        quantity, price, fee, instrument = (int(fill[name]) for name in ("quantity", "price", "fee", "instrument_id"))
        cash -= quantity * price + fee
        fees += fee
        positions[instrument] += quantity
        order = result["orders"][int(fill["order_id"]) - 1]
        if int(fill["sequence"]) <= int(order["sequence"]):
            raise AssertionError("same-event or future-order fill")
    if cash != result["cash"] or fees != result["total_fees"] or positions != result["positions"].tolist():
        raise AssertionError("fill ledger does not reconcile with portfolio")


def run(ticks, *, chunk_size=65521, seed=20260904):
    if TopOfBookBacktester is None:
        raise RuntimeError("native backtest extension required")
    if ticks <= 0 or not 0 < chunk_size <= 1048576:
        raise ValueError("positive ticks and bounded chunk size required")
    config = dict(instruments=8, cash=10**13, target_lots=1000, rebalance_events=10000,
                  fee_ppm=100, latency_ns=1_000_000, audit_capacity=1_000_000, maximum_feed_age_ns=1_000_000_000)
    start = time.perf_counter()
    engine = TopOfBookBacktester(**config)
    initialization = time.perf_counter() - start
    processing = generation = 0.0
    input_hash = hashlib.sha256()
    last_progress = start
    for offset in range(0, ticks, chunk_size):
        before = time.perf_counter()
        books = make_books(offset, min(chunk_size, ticks - offset), seed=seed)
        generation += time.perf_counter() - before
        input_hash.update(memoryview(books).cast("B"))
        before = time.perf_counter()
        if engine.process_batch(books) != len(books):
            raise AssertionError("input was not fully processed")
        processing += time.perf_counter() - before
        if time.perf_counter() - last_progress >= 30:
            print(json.dumps({"processed": offset + len(books), "target": ticks}), flush=True)
            last_progress = time.perf_counter()
    before = time.perf_counter()
    result = engine.result()
    materialization = time.perf_counter() - before
    reconcile(result, config["cash"])
    result_hash = hashlib.sha256()
    for name in ("positions", "orders", "fills"):
        result_hash.update(result[name].tobytes())
    scalars = {name: result[name] for name in ("processed", "cash", "equity", "net_pnl", "total_fees")}
    result_hash.update(json.dumps(scalars, sort_keys=True).encode())
    if result["processed"] != ticks:
        raise AssertionError("lost input")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    native_path = Path(sys.modules[TopOfBookBacktester.__module__].__file__)
    source_path = Path(__file__).parents[1] / "src/gambit/cpp/factor_cache/top_of_book_backtest.cpp"
    return {
        "status": "ledger_reconciled", "strategy": "long-only alternating target, market orders only",
        "configuration": config, "ticks": ticks, "aggregate_ticks_per_second": 10, "seed": seed,
        "chunk_size": chunk_size, "input_sha256": input_hash.hexdigest(), "result_sha256": result_hash.hexdigest(),
        "portfolio": scalars, "positions": result["positions"].tolist(),
        "order_count": len(result["orders"]), "fill_count": len(result["fills"]),
        "order_status_counts": {str(status): int(np.count_nonzero(result["orders"]["status"] == status)) for status in range(4)},
        "timing": {"initialization_seconds": initialization, "processing_seconds": processing,
                   "result_materialization_seconds": materialization,
                   "execution_seconds": initialization + processing + materialization,
                   "generation_seconds": generation, "wall_seconds": time.perf_counter() - start},
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "numpy": np.__version__},
        "provenance": {"native_extension_sha256": hashlib.sha256(native_path.read_bytes()).hexdigest(),
                       "cpp_source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                       "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        "peak_process_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "limitations": "Synthetic chunk-resident replay; excludes generation/loading, hashing and ledger verification from execution timer. Not a cold-load or full-dataset-resident benchmark. Small-corpus Python trace parity is tested separately; full-volume independent Python trace parity has not been run. Displayed liquidity refreshes per quote; no own impact, queue position, shorts, funding, limit orders, rolls or external risk policies.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticks", type=int, default=946944000)
    parser.add_argument("--chunk-size", type=int, default=65521)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    result = run(args.ticks, chunk_size=args.chunk_size)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
