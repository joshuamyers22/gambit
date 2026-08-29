"""Measure factor-cache metrics persistence and export overhead."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from gambit.factor_metrics import (
    format_prometheus_metrics,
    read_factor_cache_metrics,
    record_factor_cache_metrics,
)


@dataclass(frozen=True)
class LatencyMeasurement:
    operation: str
    samples: int
    median_microseconds: float
    p99_microseconds: float
    maximum_microseconds: float


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
    return ordered[index]


def _measure(operation: str, samples: int, callback) -> LatencyMeasurement:
    durations: list[float] = []
    for _ in range(samples):
        started = time.perf_counter_ns()
        callback()
        durations.append((time.perf_counter_ns() - started) / 1000)
    return LatencyMeasurement(
        operation,
        samples,
        statistics.median(durations),
        _percentile(durations, 0.99),
        max(durations),
    )


def _writer(root: str, samples: int) -> None:
    for _ in range(samples):
        record_factor_cache_metrics(root, cache_hits=1)


def run_benchmark(root: Path, samples: int, workers: int = 1) -> dict[str, object]:
    if samples < 2 or workers < 1:
        raise ValueError("samples must be at least two and workers must be positive")
    root.mkdir(parents=True, exist_ok=True)
    record_factor_cache_metrics(root)
    measurements = [
        _measure(
            "atomic_record",
            samples,
            lambda: record_factor_cache_metrics(root, cache_hits=1),
        ),
        _measure("locked_read", samples, lambda: read_factor_cache_metrics(root)),
        _measure(
            "openmetrics_render",
            samples,
            lambda: format_prometheus_metrics(read_factor_cache_metrics(root), openmetrics=True),
        ),
    ]
    contention_seconds = None
    if workers > 1:
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=_writer, args=(str(root), samples)) for _ in range(workers)]
        started = time.perf_counter()
        for process in processes:
            process.start()
        for process in processes:
            process.join()
            if process.exitcode != 0:
                raise RuntimeError("metrics contention worker failed")
        contention_seconds = time.perf_counter() - started
    return {
        "samples_per_operation": samples,
        "workers": workers,
        "measurements": [asdict(measurement) for measurement in measurements],
        "contention_seconds": contention_seconds,
        "final_counters": read_factor_cache_metrics(root).counters,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--cache-directory", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.samples < 2 or arguments.workers < 1:
        parser.error("samples must be at least two and workers must be positive")
    if arguments.cache_directory is None:
        with tempfile.TemporaryDirectory(prefix="gambit-metrics-benchmark-") as directory:
            result = run_benchmark(Path(directory), arguments.samples, arguments.workers)
    else:
        result = run_benchmark(arguments.cache_directory, arguments.samples, arguments.workers)
    encoded = json.dumps(result, indent=2) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded)


if __name__ == "__main__":
    main()
