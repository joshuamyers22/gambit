"""Bounded-memory, multi-year C++/Python tick-factor differential replay.

Synthetic volume equivalence only: this is not exchange-calibrated market data,
an order-book reconstruction, a strategy backtest, or a concurrent queue benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import resource
import sys
import time
from pathlib import Path

import numpy as np

from gambit.factor_cache import TICK_DTYPE, TickFactorProcessor, TickRing

START = np.datetime64("2023-01-01T00:00:00", "ns")
INTEGER_FIELDS = ("processed", "sequence_errors", "instrument_count", "maximum_latency_ns")
FLOAT_FIELDS = ("total_quantity", "total_notional", "mean_spread", "mean_mid", "mean_absolute_return")
INPUT_FIELDS = ("sequence", "event_time_ns", "receive_time_ns", "price", "quantity", "bid", "ask", "instrument_id")


def make_ticks(offset: int, count: int, rate: int, seed: int) -> np.ndarray:
    """Counter-based input: identical bytes regardless of chunk boundaries."""
    index = np.arange(offset, offset + count, dtype=np.uint64)
    # SplitMix64, with intentional unsigned modular arithmetic.
    mixed = index + np.uint64(seed)
    mixed = (mixed ^ (mixed >> 30)) * np.uint64(0xBF58476D1CE4E5B9)
    mixed = (mixed ^ (mixed >> 27)) * np.uint64(0x94D049BB133111EB)
    mixed ^= mixed >> 31
    instrument = (mixed % 8).astype(np.uint32)
    base = np.array([30000.0, 2000.0, 30.0, 0.5, 300.0, 0.1, 10.0, 100.0])[instrument]
    noise = ((mixed >> 11).astype(np.float64) / (1 << 53) - 0.5) * 0.002
    seconds = index.astype(np.float64) / rate
    cycle = 0.15 * np.sin(seconds / (86400 * 30)) + 0.03 * np.sin(seconds / 3600)
    records = np.zeros(count, dtype=TICK_DTYPE)
    records["sequence"] = index
    interval = 1_000_000_000 // rate
    records["event_time_ns"] = int(START.view("i8")) + index.astype(np.int64) * interval
    records["event_time_ns"] += (mixed % max(1, interval // 2)).astype(np.int64)
    records["receive_time_ns"] = records["event_time_ns"] + (mixed % 2_000_001).astype(np.int64)
    records["price"] = base * (1 + cycle + noise)
    records["quantity"] = 0.000001 + (mixed % 1_000_000).astype(np.float64) / 100_000
    spread = records["price"] * (0.00001 + (mixed % 20) * 0.000001)
    records["bid"] = records["price"] - spread * 0.5
    records["ask"] = records["price"] + spread * 0.5
    records["instrument_id"] = instrument
    return records


class PythonTickProcessor:
    """Sequential Python oracle; same per-tick arithmetic and accumulation order."""

    def __init__(self) -> None:
        self.processed = self.sequence_errors = self.maximum_latency_ns = 0
        self.expected_sequence: int | None = None
        self.last_prices: dict[int, float] = {}
        self.total_quantity = self.total_notional = 0.0
        self.spread_sum = self.mid_sum = self.absolute_return_sum = 0.0

    def process(self, records: np.ndarray) -> None:
        # Column conversion is included in the measured Python runtime. Python
        # scalars avoid charging the oracle repeated NumPy structured-row indexing.
        quantity_sum, notional_sum = self.total_quantity, self.total_notional
        spread_sum, mid_sum, return_sum = self.spread_sum, self.mid_sum, self.absolute_return_sum
        expected, errors, latency_max = self.expected_sequence, self.sequence_errors, self.maximum_latency_ns
        last = self.last_prices
        for sequence, event, receive, price, quantity, bid, ask, instrument in zip(
            *(records[name].tolist() for name in INPUT_FIELDS)
        ):
            if expected is not None and sequence != expected:
                errors += 1
            expected = sequence + 1
            quantity_sum += quantity
            notional_sum += price * quantity
            spread_sum += ask - bid
            mid_sum += (ask + bid) * 0.5
            previous = last.get(instrument)
            if previous is not None and previous != 0:
                return_sum += abs(price / previous - 1.0)
            last[instrument] = price
            latency = receive - event
            if latency > latency_max:
                latency_max = latency
        self.processed += len(records)
        self.total_quantity, self.total_notional = quantity_sum, notional_sum
        self.spread_sum, self.mid_sum, self.absolute_return_sum = spread_sum, mid_sum, return_sum
        self.expected_sequence, self.sequence_errors, self.maximum_latency_ns = expected, errors, latency_max

    @property
    def snapshot(self) -> dict:
        count = self.processed
        return {
            "processed": count, "sequence_errors": self.sequence_errors,
            "instrument_count": len(self.last_prices), "maximum_latency_ns": self.maximum_latency_ns,
            "total_quantity": self.total_quantity, "total_notional": self.total_notional,
            "mean_spread": self.spread_sum / count if count else 0.0,
            "mean_mid": self.mid_sum / count if count else 0.0,
            "mean_absolute_return": self.absolute_return_sum / count if count else 0.0,
        }


def compare(actual: dict, expected: dict) -> dict[str, float]:
    for name in INTEGER_FIELDS:
        if actual[name] != expected[name]:
            raise AssertionError(f"{name}: native={actual[name]} python={expected[name]}")
    errors = {}
    for name in FLOAT_FIELDS:
        if not math.isfinite(actual[name]) or not math.isfinite(expected[name]):
            raise AssertionError(f"non-finite {name}")
        if not math.isclose(actual[name], expected[name], rel_tol=1e-12, abs_tol=1e-12):
            raise AssertionError(f"{name}: native={actual[name]} python={expected[name]}")
        errors[name] = abs(actual[name] - expected[name])
    return errors


def run_replay(
    ticks: int, *, rate: int = 10, seed: int = 20260904, chunk_size: int = 65521,
    capacity: int = 65536, consume_size: int = 4093, checkpoints: tuple[int, ...] = (),
    progress_seconds: float = 30.0,
) -> dict:
    if TickRing is None or TickFactorProcessor is None:
        raise RuntimeError("compiled tick ring and factor processor are required")
    if ticks <= 0 or rate <= 0 or 1_000_000_000 % rate:
        raise ValueError("positive tick count and rate dividing one billion required")
    if capacity <= 0 or capacity & (capacity - 1) or not 0 < chunk_size <= capacity or consume_size <= 0:
        raise ValueError("power-of-two capacity, fitting positive chunk, and positive consume size required")
    if not 0 <= seed < 1 << 64:
        raise ValueError("seed must fit uint64")
    if int(START.view("i8")) + (ticks + 1) * (1_000_000_000 // rate) > np.iinfo(np.int64).max:
        raise ValueError("timeline exceeds nanosecond range")
    ring, native, reference = TickRing(capacity), TickFactorProcessor(), PythonTickProcessor()
    digest = hashlib.sha256()
    native_seconds = python_seconds = generation_seconds = 0.0
    max_errors = dict.fromkeys(FLOAT_FIELDS, 0.0)
    milestones = sorted({point for point in checkpoints if 0 < point <= ticks} | {ticks})
    milestone_index = 0
    snapshots = []
    offset = chunks = 0
    started = last_progress = time.perf_counter()
    cpu_started = time.process_time()
    first_timestamp = last_timestamp = None
    while offset < ticks:
        count = min(chunk_size, ticks - offset, milestones[milestone_index] - offset)
        before = time.perf_counter()
        records = make_ticks(offset, count, rate, seed)
        generation_seconds += time.perf_counter() - before
        digest.update(memoryview(records).cast("B"))
        if first_timestamp is None:
            first_timestamp = int(records["event_time_ns"][0])
        last_timestamp = int(records["event_time_ns"][-1])
        before = time.perf_counter()
        if ring.push_batch(records) != count:
            raise AssertionError("native ring rejected input")
        consumed = 0
        while ring.depth:
            taken = ring.process_batch(native, consume_size)
            if not taken:
                raise AssertionError("native consumer stalled")
            consumed += taken
        if consumed != count:
            raise AssertionError("native ring lost records")
        native_seconds += time.perf_counter() - before
        before = time.perf_counter()
        reference.process(records)
        python_seconds += time.perf_counter() - before
        errors = compare(native.snapshot, reference.snapshot)
        for name, error in errors.items():
            max_errors[name] = max(max_errors[name], error)
        offset += count
        chunks += 1
        if offset == milestones[milestone_index]:
            snapshots.append({
                "ticks": offset, "native": native.snapshot, "python": reference.snapshot,
                "native_seconds": native_seconds, "python_seconds": python_seconds,
                "max_absolute_errors": dict(max_errors),
            })
            milestone_index += 1
        now = time.perf_counter()
        if now - last_progress >= progress_seconds:
            print(json.dumps({"processed": offset, "target": ticks, "percent": round(offset / ticks * 100, 2),
                              "elapsed_seconds": round(now - started, 2), "parity": "pass"}), flush=True)
            last_progress = now
    metrics = ring.metrics
    if metrics["pushed"] != ticks or metrics["popped"] != ticks or metrics["dropped"] or ring.depth:
        raise AssertionError(f"ring accounting mismatch: {metrics}")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "status": "pass", "workload": "synthetic crypto-like tick-factor parity, not historical exchange data",
        "environment": {"platform": platform.platform(), "machine": platform.machine(),
                        "python": platform.python_version(), "numpy": np.__version__},
        "configuration": {"ticks": ticks, "aggregate_ticks_per_second": rate, "seed": seed,
                          "chunk_size": chunk_size, "capacity": capacity, "consume_size": consume_size,
                          "instruments": 8, "relative_tolerance": 1e-12, "absolute_tolerance": 1e-12},
        "timeline": {"nominal_start": str(START), "duration_seconds": ticks / rate,
                     "first_event_time_ns": first_timestamp, "last_event_time_ns": last_timestamp},
        "input_sha256": digest.hexdigest(), "logical_input_bytes": ticks * TICK_DTYPE.itemsize,
        "chunks_verified": chunks, "checkpoints": snapshots, "max_absolute_errors": max_errors,
        "timing": {"native_seconds": native_seconds, "python_seconds": python_seconds,
                   "native_ticks_per_second": ticks / native_seconds, "python_ticks_per_second": ticks / python_seconds,
                   "speedup": python_seconds / native_seconds, "generation_seconds": generation_seconds,
                   "wall_seconds": time.perf_counter() - started, "cpu_seconds": time.process_time() - cpu_started},
        "peak_process_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
        "ring_metrics": metrics,
        "timing_scope": "C++ includes ring push/drain; Python includes column conversion and scalar loop. Generation, hashing, and comparisons excluded from both kernel timers. One sequential interleaved trial; not transport-equivalent or concurrent throughput.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rate", type=int, default=10)
    parser.add_argument("--ticks", type=int, help="override three-calendar-year workload for smoke/pilot runs")
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; choose a new result path")
    two_years = int((np.datetime64("2025-01-01") - np.datetime64("2023-01-01")) / np.timedelta64(1, "s")) * args.rate
    three_years = int((np.datetime64("2026-01-01") - np.datetime64("2023-01-01")) / np.timedelta64(1, "s")) * args.rate
    result = run_replay(args.ticks if args.ticks is not None else three_years, rate=args.rate, seed=args.seed,
                        checkpoints=(two_years, three_years))
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "timing": result["timing"], "output": str(args.output)}), flush=True)


if __name__ == "__main__":
    main()
