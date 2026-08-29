"""Compare native bounded-spin/yield/park tick transport with blocking queues."""

from __future__ import annotations

import argparse
import json
import platform
import queue
import statistics
import threading
import time
from dataclasses import asdict, dataclass

import numpy as np

from gambit.factor_cache import TICK_DTYPE, TickFactorProcessor, TickRing


@dataclass(frozen=True)
class PipelineMeasurement:
    name: str
    ticks: int
    batch_size: int
    capacity: int
    wall_seconds: float
    cpu_seconds: float
    ticks_per_second: float
    sequence_errors: int
    rejected_pushes: int
    spins: int
    parks: int
    park_timeout_seconds: float


def make_ticks(count: int) -> np.ndarray:
    records = np.zeros(count, dtype=TICK_DTYPE)
    records["sequence"] = np.arange(count, dtype=np.uint64)
    records["event_time_ns"] = records["sequence"].astype(np.int64) * 100
    records["receive_time_ns"] = records["event_time_ns"] + 10
    records["price"] = 100 + np.sin(np.arange(count) / 1000)
    records["quantity"] = 1
    records["bid"] = records["price"] - 0.01
    records["ask"] = records["price"] + 0.01
    records["instrument_id"] = 1
    return records


def benchmark_native(
    records: np.ndarray,
    batch_size: int,
    capacity: int,
    spin_count: int = 256,
    park_timeout_seconds: float = 0.01,
) -> PipelineMeasurement:
    if TickRing is None:
        raise RuntimeError("native tick ring extension is not built")
    ring = TickRing(capacity)
    sequence_errors = 0
    consumed = 0

    def produce() -> None:
        offset = 0
        while offset < len(records):
            end = min(offset + batch_size, len(records))
            count = end - offset
            while ring.capacity - ring.depth < count:
                time.sleep(0)
            pushed = ring.push_batch(records[offset:end])
            if pushed != count:
                raise RuntimeError("producer capacity invariant failed")
            offset = end

    def consume() -> None:
        nonlocal consumed, sequence_errors
        expected = 0
        while consumed < len(records):
            batch = ring.wait_pop_batch(
                batch_size,
                spin_count=spin_count,
                timeout_seconds=park_timeout_seconds,
            )
            if not len(batch):
                continue
            sequences = batch["sequence"]
            expected_values = np.arange(expected, expected + len(batch), dtype=np.uint64)
            sequence_errors += int(np.count_nonzero(sequences != expected_values))
            expected += len(batch)
            consumed += len(batch)

    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    producer = threading.Thread(target=produce)
    consumer = threading.Thread(target=consume)
    consumer.start()
    producer.start()
    producer.join()
    consumer.join()
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    metrics = ring.metrics
    return PipelineMeasurement(
        "native_spsc_batch",
        len(records),
        batch_size,
        capacity,
        wall,
        cpu,
        len(records) / wall,
        sequence_errors,
        metrics["dropped"],
        metrics["spins"],
        metrics["parks"],
        park_timeout_seconds,
    )


def benchmark_native_in_place(
    records: np.ndarray,
    batch_size: int,
    capacity: int,
    spin_count: int = 256,
    park_timeout_seconds: float = 0.01,
) -> PipelineMeasurement:
    if TickRing is None or TickFactorProcessor is None:
        raise RuntimeError("native tick processor extension is not built")
    ring = TickRing(capacity)
    processor = TickFactorProcessor()
    consumed = 0

    def produce() -> None:
        offset = 0
        while offset < len(records):
            end = min(offset + batch_size, len(records))
            count = end - offset
            while ring.capacity - ring.depth < count:
                time.sleep(0)
            pushed = ring.push_batch(records[offset:end])
            if pushed != count:
                raise RuntimeError("producer capacity invariant failed")
            offset = end

    def consume() -> None:
        nonlocal consumed
        while consumed < len(records):
            consumed += ring.wait_process_batch(
                processor,
                batch_size,
                spin_count=spin_count,
                timeout_seconds=park_timeout_seconds,
            )

    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    producer = threading.Thread(target=produce)
    consumer = threading.Thread(target=consume)
    consumer.start()
    producer.start()
    producer.join()
    consumer.join()
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    metrics = ring.metrics
    snapshot = processor.snapshot
    return PipelineMeasurement(
        "native_spsc_in_place_factors",
        len(records),
        batch_size,
        capacity,
        wall,
        cpu,
        len(records) / wall,
        snapshot["sequence_errors"],
        metrics["dropped"],
        metrics["spins"],
        metrics["parks"],
        park_timeout_seconds,
    )


def benchmark_python_queue(records: np.ndarray, capacity: int) -> PipelineMeasurement:
    bounded_queue: queue.Queue[int] = queue.Queue(maxsize=capacity)
    sequence_errors = 0

    def produce() -> None:
        for sequence in records["sequence"]:
            bounded_queue.put(int(sequence))

    def consume() -> None:
        nonlocal sequence_errors
        for expected in range(len(records)):
            sequence = bounded_queue.get()
            sequence_errors += int(sequence != expected)

    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    producer = threading.Thread(target=produce)
    consumer = threading.Thread(target=consume)
    consumer.start()
    producer.start()
    producer.join()
    consumer.join()
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    return PipelineMeasurement(
        "python_queue_per_tick",
        len(records),
        1,
        capacity,
        wall,
        cpu,
        len(records) / wall,
        sequence_errors,
        0,
        0,
        0.0,
        0,
    )


def benchmark_python_batch_queue(records: np.ndarray, batch_size: int, capacity: int) -> PipelineMeasurement:
    bounded_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max(1, capacity // batch_size))
    sequence_errors = 0
    calculation_guard = 0.0

    def produce() -> None:
        for offset in range(0, len(records), batch_size):
            bounded_queue.put(records[offset : offset + batch_size])

    def consume() -> None:
        nonlocal calculation_guard, sequence_errors
        expected = 0
        previous_price: float | None = None
        while expected < len(records):
            batch = bounded_queue.get()
            expected_values = np.arange(expected, expected + len(batch), dtype=np.uint64)
            sequence_errors += int(np.count_nonzero(batch["sequence"] != expected_values))
            calculation_guard += float(np.dot(batch["price"], batch["quantity"]))
            calculation_guard += float((batch["ask"] - batch["bid"]).sum())
            calculation_guard += float(((batch["ask"] + batch["bid"]) * 0.5).sum())
            if previous_price is not None:
                calculation_guard += abs(float(batch["price"][0]) / previous_price - 1)
            if len(batch) > 1:
                calculation_guard += float(np.abs(batch["price"][1:] / batch["price"][:-1] - 1).sum())
            previous_price = float(batch["price"][-1])
            expected += len(batch)

    cpu_start = time.process_time()
    wall_start = time.perf_counter()
    producer = threading.Thread(target=produce)
    consumer = threading.Thread(target=consume)
    consumer.start()
    producer.start()
    producer.join()
    consumer.join()
    wall = time.perf_counter() - wall_start
    cpu = time.process_time() - cpu_start
    return PipelineMeasurement(
        "python_queue_batch",
        len(records),
        batch_size,
        capacity,
        wall,
        cpu,
        len(records) / wall,
        sequence_errors,
        0,
        0,
        0.0,
        0,
    )


def run_benchmark(
    ticks: int,
    batch_size: int,
    capacity: int,
    spin_count: int = 256,
    park_timeout_seconds: float = 0.01,
) -> dict[str, object]:
    if capacity < batch_size:
        raise ValueError("capacity must be at least batch_size")
    if not np.isfinite(park_timeout_seconds) or park_timeout_seconds < 0:
        raise ValueError("park_timeout_seconds must be finite and non-negative")
    records = make_ticks(ticks)
    measurements = [
        benchmark_python_queue(records, capacity),
        benchmark_python_batch_queue(records, batch_size, capacity),
    ]
    if TickRing is not None:
        measurements.append(
            benchmark_native(records, batch_size, capacity, spin_count, park_timeout_seconds)
        )
    if TickRing is not None and TickFactorProcessor is not None:
        measurements.append(
            benchmark_native_in_place(
                records,
                batch_size,
                capacity,
                spin_count,
                park_timeout_seconds,
            )
        )
    return {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "measurements": [asdict(measurement) for measurement in measurements],
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def run_matrix(
    ticks: int,
    batch_sizes: list[int],
    capacities: list[int],
    spin_counts: list[int],
    park_timeouts: list[float],
    repeats: int,
    warmups: int,
) -> dict[str, object]:
    if ticks <= 0 or repeats <= 0 or warmups < 0:
        raise ValueError("ticks and repeats must be positive and warmups must be non-negative")
    if not batch_sizes or not capacities or not spin_counts or not park_timeouts:
        raise ValueError("matrix dimensions must not be empty")
    configurations: list[dict[str, object]] = []
    for batch_size in batch_sizes:
        for capacity in capacities:
            if batch_size <= 0 or capacity < batch_size or capacity & (capacity - 1):
                raise ValueError("capacity must be a power of two and at least batch_size")
            for spin_count in spin_counts:
                if spin_count < 0:
                    raise ValueError("spin_count must be non-negative")
                for park_timeout in park_timeouts:
                    if not np.isfinite(park_timeout) or park_timeout < 0:
                        raise ValueError("park timeouts must be finite and non-negative")
                    for _ in range(warmups):
                        run_benchmark(ticks, batch_size, capacity, spin_count, park_timeout)
                    trials = [
                        run_benchmark(ticks, batch_size, capacity, spin_count, park_timeout)
                        for _ in range(repeats)
                    ]
                    names = [measurement["name"] for measurement in trials[0]["measurements"]]
                    summaries = []
                    for name in names:
                        samples = [
                            next(item for item in trial["measurements"] if item["name"] == name)
                            for trial in trials
                        ]
                        throughput = [float(sample["ticks_per_second"]) for sample in samples]
                        wall = [float(sample["wall_seconds"]) for sample in samples]
                        cpu_efficiency = [
                            float(sample["cpu_seconds"]) / float(sample["wall_seconds"])
                            for sample in samples
                        ]
                        summaries.append(
                            {
                                "name": name,
                                "median_ticks_per_second": statistics.median(throughput),
                                "p95_ticks_per_second": _percentile(throughput, 0.95),
                                "p50_trial_latency_seconds": _percentile(wall, 0.50),
                                "p99_trial_latency_seconds": _percentile(wall, 0.99),
                                "median_cpu_to_wall_ratio": statistics.median(cpu_efficiency),
                                "min_ticks_per_second": min(throughput),
                                "max_ticks_per_second": max(throughput),
                                "median_spins": statistics.median(
                                    int(sample["spins"]) for sample in samples
                                ),
                                "median_parks": statistics.median(
                                    int(sample["parks"]) for sample in samples
                                ),
                                "sequence_errors": sum(
                                    int(sample["sequence_errors"]) for sample in samples
                                ),
                                "rejected_pushes": sum(
                                    int(sample["rejected_pushes"]) for sample in samples
                                ),
                            }
                        )
                    configurations.append(
                        {
                            "batch_size": batch_size,
                            "capacity": capacity,
                            "spin_count": spin_count,
                            "park_timeout_seconds": park_timeout,
                            "measurements": summaries,
                        }
                    )
    return {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "workload": {"ticks": ticks, "repeats": repeats, "warmups": warmups},
        "configurations": configurations,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticks", type=int, default=1_000_000)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--capacity", type=int, default=65536)
    parser.add_argument("--spin-count", type=int, default=256)
    parser.add_argument("--park-timeout", type=float, default=0.01)
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[64, 256, 1024])
    parser.add_argument("--capacities", type=int, nargs="+", default=[4096, 65536])
    parser.add_argument("--spin-counts", type=int, nargs="+", default=[0, 64, 256, 1024])
    parser.add_argument("--park-timeouts", type=float, nargs="+", default=[0.0, 0.0001, 0.001, 0.01])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--output")
    arguments = parser.parse_args()
    if arguments.ticks <= 0 or arguments.batch_size <= 0:
        parser.error("ticks and batch-size must be positive")
    if arguments.matrix:
        result = run_matrix(
            arguments.ticks,
            arguments.batch_sizes,
            arguments.capacities,
            arguments.spin_counts,
            arguments.park_timeouts,
            arguments.repeats,
            arguments.warmups,
        )
    else:
        result = run_benchmark(
            arguments.ticks,
            arguments.batch_size,
            arguments.capacity,
            arguments.spin_count,
            arguments.park_timeout,
        )
    output = json.dumps(result, indent=2)
    if arguments.output:
        with open(arguments.output, "w") as file:
            file.write(output + "\n")
    else:
        print(output)


if __name__ == "__main__":
    main()
