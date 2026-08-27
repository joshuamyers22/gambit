"""Benchmark factor computation and mapped-column cache alternatives.

Run from the repository root after building the native extensions::

    .venv/bin/python benchmarks/factor_cache_benchmark.py --rows 1000000 --repeats 3
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import polars as pl

from gambit.factor_cache import MappedFloat64Column


@dataclass(frozen=True)
class Measurement:
    name: str
    rows: int
    columns: int
    bytes: int
    median_seconds: float
    minimum_seconds: float
    median_cpu_seconds: float
    throughput_mib_second: float
    minor_page_faults: int
    major_page_faults: int


def make_market_data(rows: int, seed: int = 7) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 0.001, rows)
    prices = 100 * np.exp(np.cumsum(returns))
    volume = rng.lognormal(10, 0.5, rows)
    return pl.DataFrame({"price": prices, "volume": volume})


def calculate_factor_tree(data: pl.DataFrame) -> pl.DataFrame:
    """A branching DAG whose return and moving-average ancestors are shared."""
    return (
        data.lazy()
        .with_columns(pl.col("price").log().diff().alias("return"))
        .with_columns(
            pl.col("return").rolling_mean(20).alias("return_mean_20"),
            pl.col("return").rolling_std(20).alias("return_std_20"),
            pl.col("price").rolling_mean(50).alias("price_mean_50"),
        )
        .with_columns(
            ((pl.col("return") - pl.col("return_mean_20")) / pl.col("return_std_20")).alias("return_zscore"),
            (pl.col("price") / pl.col("price_mean_50") - 1).alias("momentum_50"),
            (pl.col("volume") * pl.col("return").abs()).alias("volume_impulse"),
        )
        .select("return", "return_mean_20", "return_std_20", "price_mean_50", "return_zscore", "momentum_50", "volume_impulse")
        .collect()
    )


def sum_frame(frame: pl.DataFrame) -> float:
    return sum(float(series.sum() or 0.0) for series in frame)


def _measure(name: str, rows: int, columns: int, byte_count: int, operation: Callable[[], float], repeats: int) -> Measurement:
    wall_times: list[float] = []
    cpu_times: list[float] = []
    minor_faults = 0
    major_faults = 0
    guard = 0.0
    for _ in range(repeats):
        usage_before = resource.getrusage(resource.RUSAGE_SELF)
        cpu_before = time.process_time()
        wall_before = time.perf_counter()
        guard += operation()
        wall_times.append(time.perf_counter() - wall_before)
        cpu_times.append(time.process_time() - cpu_before)
        usage_after = resource.getrusage(resource.RUSAGE_SELF)
        minor_faults += usage_after.ru_minflt - usage_before.ru_minflt
        major_faults += usage_after.ru_majflt - usage_before.ru_majflt
    if not np.isfinite(guard):
        guard = 0.0
    median_seconds = statistics.median(wall_times)
    return Measurement(
        name,
        rows,
        columns,
        byte_count,
        median_seconds,
        min(wall_times),
        statistics.median(cpu_times),
        byte_count / (1024 * 1024) / median_seconds if median_seconds else float("inf"),
        minor_faults,
        major_faults,
    )


def run_benchmark(rows: int, repeats: int, cache_directory: Path) -> dict[str, object]:
    cache_directory.mkdir(parents=True, exist_ok=True)
    data = make_market_data(rows)
    factors = calculate_factor_tree(data)
    columns = factors.width
    byte_count = sum(series.n_chunks() and series.len() * 8 for series in factors)
    measurements: list[Measurement] = []

    measurements.append(
        _measure(
            "polars_factor_dag",
            rows,
            columns,
            byte_count,
            lambda: float(calculate_factor_tree(data)["return_zscore"].sum()),
            repeats,
        )
    )

    ipc_path = cache_directory / "factors.arrow"
    measurements.append(
        _measure(
            "polars_ipc_write",
            rows,
            columns,
            byte_count,
            lambda: (factors.write_ipc(ipc_path), float(ipc_path.stat().st_size))[1],
            1,
        )
    )
    measurements.append(
        _measure(
            "polars_ipc_mmap_read",
            rows,
            columns,
            byte_count,
            lambda: sum_frame(pl.read_ipc(ipc_path, memory_map=True)),
            repeats,
        )
    )
    resident_ipc = pl.read_ipc(ipc_path, memory_map=True)
    measurements.append(
        _measure(
            "polars_ipc_resident_read",
            rows,
            columns,
            byte_count,
            lambda: sum_frame(resident_ipc),
            repeats,
        )
    )

    parquet_path = cache_directory / "factors.parquet"
    measurements.append(
        _measure(
            "polars_parquet_write",
            rows,
            columns,
            byte_count,
            lambda: (factors.write_parquet(parquet_path), float(parquet_path.stat().st_size))[1],
            1,
        )
    )
    measurements.append(
        _measure(
            "polars_parquet_read",
            rows,
            columns,
            byte_count,
            lambda: sum_frame(pl.read_parquet(parquet_path)),
            repeats,
        )
    )

    raw_paths = [cache_directory / f"raw-{index}.bin" for index in range(columns)]

    def raw_write() -> float:
        total = 0.0
        for path, series in zip(raw_paths, factors):
            mapped = np.memmap(path, mode="w+", dtype=np.float64, shape=(rows,))
            mapped[:] = series.to_numpy()
            mapped.flush()
            total += float(mapped[-1])
        return total

    measurements.append(_measure("numpy_raw_mmap_write", rows, columns, byte_count, raw_write, 1))

    def raw_read() -> float:
        return sum(float(np.memmap(path, mode="r", dtype=np.float64, shape=(rows,)).sum()) for path in raw_paths)

    measurements.append(_measure("numpy_raw_mmap_reopen_read", rows, columns, byte_count, raw_read, repeats))
    resident_raw = [np.memmap(path, mode="r", dtype=np.float64, shape=(rows,)) for path in raw_paths]
    measurements.append(
        _measure(
            "numpy_raw_mmap_resident_read",
            rows,
            columns,
            byte_count,
            lambda: sum(float(column.sum()) for column in resident_raw),
            repeats,
        )
    )

    if MappedFloat64Column is not None:
        native_paths = [cache_directory / f"native-{index}.bin" for index in range(columns)]

        def native_write() -> float:
            total = 0.0
            for path, series in zip(native_paths, factors):
                column = MappedFloat64Column.create(str(path), series.to_numpy())
                total += float(column.values[-1])
            return total

        measurements.append(_measure("native_committed_mmap_write", rows, columns, byte_count, native_write, 1))

        native_columns = [MappedFloat64Column.open(str(path)) for path in native_paths]

        def native_reopen_read() -> float:
            columns_to_read = [MappedFloat64Column.open(str(path)) for path in native_paths]
            return sum(float(column.values.sum()) for column in columns_to_read)

        measurements.append(
            _measure("native_committed_mmap_reopen_read", rows, columns, byte_count, native_reopen_read, repeats)
        )

        def native_read() -> float:
            return sum(float(column.values.sum()) for column in native_columns)

        measurements.append(_measure("native_committed_mmap_resident_read", rows, columns, byte_count, native_read, repeats))

    return {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "polars": pl.__version__,
            "numpy": np.__version__,
            "cache_directory": str(cache_directory),
        },
        "workload": {"rows": rows, "factor_columns": columns, "logical_bytes": byte_count, "repeats": repeats},
        "measurements": [asdict(measurement) for measurement in measurements],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, nargs="+", default=[1_000_000, 10_000_000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cache-directory", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if any(rows <= 100 for rows in arguments.rows) or arguments.repeats <= 0:
        parser.error("rows must exceed 100 and repeats must be positive")

    results: list[dict[str, object]] = []
    if arguments.cache_directory is None:
        with tempfile.TemporaryDirectory(prefix="gambit-factor-benchmark-") as directory:
            for rows in arguments.rows:
                results.append(run_benchmark(rows, arguments.repeats, Path(directory) / str(rows)))
    else:
        for rows in arguments.rows:
            results.append(run_benchmark(rows, arguments.repeats, arguments.cache_directory / str(rows)))
    output = json.dumps(results, indent=2)
    if arguments.output is None:
        print(output)
    else:
        arguments.output.write_text(output + "\n")


if __name__ == "__main__":
    main()
