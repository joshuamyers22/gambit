"""Benchmark verified v2-to-v3 factor-generation migration."""

from __future__ import annotations

import argparse
import json
import resource
import tempfile
import time
from pathlib import Path

import numpy as np

import gambit.factor_store as factor_store
from gambit.factor_cache import MappedFloat64Column
from gambit.factor_device import FactorCacheDeviceTelemetry, inspect_factor_cache_device
from gambit.factor_identity import FactorColumnSchema, FactorNodeIdentity
from gambit.factor_operations import inspect_factor_cache
from gambit.factor_store import (
    collect_garbage,
    migrate_factor_nodes_to_v3,
    open_generation_by_node_key,
    publish_factor_node,
)


def _device_write_delta(
    before: FactorCacheDeviceTelemetry,
    after: FactorCacheDeviceTelemetry,
) -> int | None:
    if (
        not before.available
        or not after.available
        or before.source != after.source
        or before.device_bytes_written is None
        or after.device_bytes_written is None
        or after.device_bytes_written < before.device_bytes_written
    ):
        return None
    return after.device_bytes_written - before.device_bytes_written


def _publish_v2(root: Path, identity: FactorNodeIdentity, columns: dict[str, np.ndarray]) -> str:
    original = factor_store.MappedFloat64Column.create_chunked_v3
    factor_store.MappedFloat64Column.create_chunked_v3 = factor_store.MappedFloat64Column.create_chunked
    try:
        return publish_factor_node(root, identity, columns)
    finally:
        factor_store.MappedFloat64Column.create_chunked_v3 = original


def run_benchmark(rows: int, columns: int, root: Path) -> dict[str, object]:
    if rows < 1 or columns < 1:
        raise ValueError("rows and columns must be positive")
    if MappedFloat64Column is None:
        raise RuntimeError("native factor cache extension is not built")
    root.mkdir(parents=True, exist_ok=True)
    values = {
        f"factor_{index}": np.arange(rows, dtype=np.float64) + index
        for index in range(columns)
    }
    identity = FactorNodeIdentity(
        transform="gambit.benchmark.migration",
        transform_version=f"rows-{rows}-columns-{columns}",
        input_fingerprints={"source": "a" * 64},
        output_schema=tuple(FactorColumnSchema(name, "float64") for name in values),
        row_ordering=("row_index",),
    )
    old_generation = _publish_v2(root, identity, values)
    before_inventory = inspect_factor_cache(root)
    plan = migrate_factor_nodes_to_v3(root)
    device_before = inspect_factor_cache_device(root)
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    cpu_before = time.process_time()
    started = time.perf_counter()
    migration = migrate_factor_nodes_to_v3(root, dry_run=False)
    elapsed_seconds = time.perf_counter() - started
    cpu_seconds = time.process_time() - cpu_before
    usage_after = resource.getrusage(resource.RUSAGE_SELF)
    device_after = inspect_factor_cache_device(root)
    peak_inventory = inspect_factor_cache(root)
    migrated = migration["migrated_nodes"]
    if len(migrated) != 1:
        raise RuntimeError("benchmark expected exactly one migrated node")
    new_generation = migrated[0]["new_generation"]
    with open_generation_by_node_key(root, identity.node_key) as lease:
        equality = all(np.array_equal(lease[name].values, values[name]) for name in values)
        versions = sorted({lease[name].format_version for name in values})
    collection = collect_garbage(root, dry_run=False)
    after_collection = inspect_factor_cache(root)
    logical_bytes = rows * columns * 8
    allocation_delta = peak_inventory.total_cache_allocated_bytes - before_inventory.total_cache_allocated_bytes
    device_delta = _device_write_delta(device_before, device_after)
    return {
        "workload": {"rows": rows, "columns": columns, "logical_bytes": logical_bytes},
        "migration": {
            "old_generation": old_generation,
            "new_generation": new_generation,
            "elapsed_seconds": elapsed_seconds,
            "cpu_seconds": cpu_seconds,
            "minor_page_faults": usage_after.ru_minflt - usage_before.ru_minflt,
            "major_page_faults": usage_after.ru_majflt - usage_before.ru_majflt,
            "planned_write_bytes": plan["planned_write_bytes"],
            "temporary_allocated_bytes": allocation_delta,
            "host_allocation_write_amplification": allocation_delta / logical_bytes,
            "device_bytes_written_delta": device_delta,
            "device_write_amplification": device_delta / logical_bytes if device_delta is not None else None,
            "exact_equality": equality,
            "segment_versions": versions,
        },
        "allocation": {
            "before_bytes": before_inventory.total_cache_allocated_bytes,
            "peak_bytes": peak_inventory.total_cache_allocated_bytes,
            "after_collection_bytes": after_collection.total_cache_allocated_bytes,
        },
        "collection": collection,
        "device_before": device_before.snapshot(),
        "device_after": device_after.snapshot(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, nargs="+", default=[1_000_000, 10_000_000])
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("--cache-directory", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if any(rows < 1 for rows in arguments.rows) or arguments.columns < 1:
        parser.error("rows and columns must be positive")
    results: list[dict[str, object]] = []
    if arguments.cache_directory is None:
        with tempfile.TemporaryDirectory(prefix="gambit-migration-benchmark-") as directory:
            for rows in arguments.rows:
                results.append(run_benchmark(rows, arguments.columns, Path(directory) / str(rows)))
    else:
        for rows in arguments.rows:
            results.append(run_benchmark(rows, arguments.columns, arguments.cache_directory / str(rows)))
    encoded = json.dumps(results, indent=2) + "\n"
    if arguments.output is None:
        print(encoded, end="")
    else:
        arguments.output.write_text(encoded)


if __name__ == "__main__":
    main()
