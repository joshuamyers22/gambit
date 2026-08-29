"""Operational inventory and on-device calibration for the experimental factor cache."""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from gambit.factor_cache import MappedFloat64Column
from gambit.factor_dag import FactorCacheAdmissionPolicy

_NODE_KEY_PATTERN = re.compile(r"[0-9a-f]{64}")
_GENERATION_PATTERN = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class FactorCacheFinding:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class FactorCacheNodeInventory:
    node_key: str
    generation: str
    stored_bytes: int
    allocated_bytes: int
    column_count: int
    row_count: int | None
    last_access_ns: int | None
    sampled_access_count: int
    lease_count: int
    current: bool


@dataclass(frozen=True)
class FactorCacheInventory:
    root: str
    measured_at_ns: int
    device_id: int
    filesystem_block_size: int
    filesystem_capacity_bytes: int
    filesystem_free_bytes: int
    total_cache_file_bytes: int
    total_cache_allocated_bytes: int
    indexed_generation_bytes: int
    indexed_nodes: tuple[FactorCacheNodeInventory, ...]
    generation_count: int
    unindexed_generation_count: int
    staging_generation_count: int
    lease_file_count: int
    rejection_hint_count: int
    access_record_count: int
    findings: tuple[FactorCacheFinding, ...]
    device_wear_measured: bool = False

    def snapshot(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FactorCacheCalibration:
    root: str
    measured_at_ns: int
    device_id: int
    sample_bytes: tuple[int, int]
    repeats: int
    estimated_read_bytes_per_second: float
    estimated_write_bytes_per_second: float
    fixed_read_seconds: float
    fixed_write_seconds: float
    page_cache_eviction_requested: bool
    page_cache_eviction_is_advisory: bool = True
    device_wear_measured: bool = False

    def admission_policy(
        self,
        *,
        minimum_expected_uses: int = 2,
        minimum_speedup: float = 1.1,
        rejection_ttl_seconds: float = 3600.0,
    ) -> FactorCacheAdmissionPolicy:
        return FactorCacheAdmissionPolicy(
            minimum_expected_uses=minimum_expected_uses,
            estimated_read_bytes_per_second=self.estimated_read_bytes_per_second,
            estimated_write_bytes_per_second=self.estimated_write_bytes_per_second,
            fixed_read_seconds=self.fixed_read_seconds,
            fixed_write_seconds=self.fixed_write_seconds,
            minimum_speedup=minimum_speedup,
            rejection_ttl_seconds=rejection_ttl_seconds,
        )

    def snapshot(self) -> dict[str, object]:
        return asdict(self)


def _file_sizes(path: Path) -> tuple[int, int]:
    stored = 0
    allocated = 0
    for entry in path.rglob("*"):
        if entry.is_file() and not entry.is_symlink():
            stat = entry.stat()
            stored += stat.st_size
            allocated += stat.st_blocks * 512
    return stored, allocated


def inspect_factor_cache(root: str | Path) -> FactorCacheInventory:
    """Return a non-mutating inventory while reporting malformed advisory state."""
    store = Path(root)
    if not store.is_dir() or store.is_symlink():
        raise ValueError("factor cache root must be an existing non-symlink directory")
    measured_at_ns = time.time_ns()
    findings: list[FactorCacheFinding] = []
    try:
        current = (store / "CURRENT").read_text().strip()
    except OSError:
        current = ""
        findings.append(FactorCacheFinding("missing-current", "CURRENT", "CURRENT is missing or unreadable"))
    if current and _GENERATION_PATTERN.fullmatch(current) is None:
        findings.append(FactorCacheFinding("invalid-current", "CURRENT", "CURRENT is not a generation identifier"))

    generations_root = store / "generations"
    generation_names: set[str] = set()
    staging_count = 0
    if generations_root.is_dir() and not generations_root.is_symlink():
        for path in generations_root.iterdir():
            if path.name.startswith(".staging-"):
                staging_count += 1
            elif _GENERATION_PATTERN.fullmatch(path.name) and path.is_dir() and not path.is_symlink():
                generation_names.add(path.name)

    indexed_nodes: list[FactorCacheNodeInventory] = []
    indexed_generations: set[str] = set()
    nodes_root = store / "nodes"
    if nodes_root.is_symlink():
        findings.append(FactorCacheFinding("symlinked-index", "nodes", "node index is a symbolic link"))
    elif nodes_root.is_dir():
        for pointer in sorted(nodes_root.iterdir()):
            node_key = pointer.name
            if node_key.startswith("."):
                continue
            if _NODE_KEY_PATTERN.fullmatch(node_key) is None or pointer.is_symlink():
                findings.append(FactorCacheFinding("invalid-node-index", str(pointer), "invalid node index entry"))
                continue
            try:
                generation = pointer.read_text().strip()
            except OSError:
                findings.append(FactorCacheFinding("unreadable-node-index", str(pointer), "node index is unreadable"))
                continue
            generation_path = generations_root / generation
            if (
                _GENERATION_PATTERN.fullmatch(generation) is None
                or not generation_path.is_dir()
                or generation_path.is_symlink()
            ):
                findings.append(FactorCacheFinding("dangling-node-index", str(pointer), "generation is missing"))
                continue
            manifest_path = generation_path / "manifest.json"
            try:
                manifest = json.loads(manifest_path.read_bytes())
                columns = manifest["columns"]
                if (
                    manifest["node_key"] != node_key
                    or manifest["generation"] != generation
                    or not isinstance(columns, dict)
                    or not columns
                ):
                    raise ValueError
                row_counts = {int(value["rows"]) for value in columns.values()}
            except (OSError, ValueError, KeyError, TypeError):
                findings.append(FactorCacheFinding("invalid-manifest", str(manifest_path), "manifest is invalid"))
                continue
            stored_bytes, allocated_bytes = _file_sizes(generation_path)
            access_path = store / "access" / f"{node_key}.json"
            last_access_ns: int | None = None
            sampled_access_count = 0
            if access_path.is_file() and not access_path.is_symlink():
                try:
                    access = json.loads(access_path.read_bytes())
                    if access.get("node_key") == node_key and access.get("generation") == generation:
                        last_access_ns = int(access["last_access_ns"])
                        sampled_access_count = int(access["access_count"])
                        if (
                            last_access_ns < 0
                            or last_access_ns > measured_at_ns
                            or sampled_access_count < 0
                        ):
                            raise ValueError
                except (OSError, ValueError, KeyError, TypeError):
                    findings.append(FactorCacheFinding("invalid-access", str(access_path), "access record is invalid"))
            lease_directory = store / "leases" / generation
            if lease_directory.is_symlink():
                findings.append(
                    FactorCacheFinding("symlinked-leases", str(lease_directory), "lease directory is a symbolic link")
                )
                lease_count = 1
            else:
                lease_count = len(list(lease_directory.iterdir())) if lease_directory.is_dir() else 0
            indexed_generations.add(generation)
            indexed_nodes.append(
                FactorCacheNodeInventory(
                    node_key=node_key,
                    generation=generation,
                    stored_bytes=stored_bytes,
                    allocated_bytes=allocated_bytes,
                    column_count=len(columns),
                    row_count=next(iter(row_counts)) if len(row_counts) == 1 else None,
                    last_access_ns=last_access_ns,
                    sampled_access_count=sampled_access_count,
                    lease_count=lease_count,
                    current=generation == current,
                )
            )

    total_stored, total_allocated = _file_sizes(store)
    filesystem = os.statvfs(store)
    leases_root = store / "leases"
    lease_files = sum(1 for path in leases_root.rglob("*") if path.is_file()) if leases_root.is_dir() else 0
    admission_root = store / "admission"
    access_root = store / "access"
    return FactorCacheInventory(
        root=str(store.resolve()),
        measured_at_ns=measured_at_ns,
        device_id=store.stat().st_dev,
        filesystem_block_size=filesystem.f_frsize,
        filesystem_capacity_bytes=filesystem.f_blocks * filesystem.f_frsize,
        filesystem_free_bytes=filesystem.f_bavail * filesystem.f_frsize,
        total_cache_file_bytes=total_stored,
        total_cache_allocated_bytes=total_allocated,
        indexed_generation_bytes=sum(node.stored_bytes for node in indexed_nodes),
        indexed_nodes=tuple(indexed_nodes),
        generation_count=len(generation_names),
        unindexed_generation_count=len(generation_names - indexed_generations),
        staging_generation_count=staging_count,
        lease_file_count=lease_files,
        rejection_hint_count=(
            sum(1 for path in admission_root.glob("*.json") if path.is_file()) if admission_root.is_dir() else 0
        ),
        access_record_count=(
            sum(1 for path in access_root.glob("*.json") if path.is_file()) if access_root.is_dir() else 0
        ),
        findings=tuple(findings),
    )


def _request_page_cache_eviction(path: Path) -> bool:
    if not hasattr(os, "posix_fadvise") or not hasattr(os, "POSIX_FADV_DONTNEED"):
        return False
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.posix_fadvise(descriptor, 0, 0, int(getattr(os, "POSIX_FADV_DONTNEED")))
    finally:
        os.close(descriptor)
    return True


def _fit_cost(sample_bytes: tuple[int, int], seconds: tuple[float, float]) -> tuple[float, float]:
    byte_delta = sample_bytes[1] - sample_bytes[0]
    second_delta = seconds[1] - seconds[0]
    if second_delta > 0:
        seconds_per_byte = second_delta / byte_delta
        bandwidth = 1 / seconds_per_byte
        fixed = max(0.0, seconds[0] - sample_bytes[0] * seconds_per_byte)
        return bandwidth, fixed
    return sample_bytes[1] / max(seconds[1], 1e-12), 0.0


def calibrate_factor_cache(
    root: str | Path,
    *,
    sample_bytes: tuple[int, int] = (1024 * 1024, 16 * 1024 * 1024),
    repeats: int = 3,
    request_page_cache_eviction: bool = True,
) -> FactorCacheCalibration:
    """Measure native segment costs on the filesystem selected by ``root``."""
    if MappedFloat64Column is None:
        raise RuntimeError("native mapped-column support is unavailable")
    if (
        len(sample_bytes) != 2
        or any(type(value) is not int or value < 4096 or value % 8 for value in sample_bytes)
        or sample_bytes[0] >= sample_bytes[1]
    ):
        raise ValueError("sample_bytes must contain two increasing, float64-aligned sizes")
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be positive")
    store = Path(root)
    store.mkdir(parents=True, exist_ok=True)
    if store.is_symlink():
        raise ValueError("calibration root may not be a symbolic link")
    calibration_root = store / f".calibration-{uuid.uuid4().hex}"
    calibration_root.mkdir()
    write_medians: list[float] = []
    read_medians: list[float] = []
    eviction_requested = False
    try:
        for byte_count in sample_bytes:
            values: NDArray[np.float64] = np.arange(byte_count // 8, dtype=np.float64)
            write_times: list[float] = []
            paths: list[Path] = []
            for repeat in range(repeats):
                path = calibration_root / f"{byte_count}-{repeat}.bin"
                started = time.perf_counter()
                MappedFloat64Column.create_chunked(str(path), values)
                write_times.append(time.perf_counter() - started)
                paths.append(path)
            read_times: list[float] = []
            for path in paths:
                if request_page_cache_eviction:
                    eviction_requested = _request_page_cache_eviction(path) or eviction_requested
                started = time.perf_counter()
                guard = float(MappedFloat64Column.open(str(path)).values.sum())
                read_times.append(time.perf_counter() - started)
                if not np.isfinite(guard):
                    raise RuntimeError("calibration produced a non-finite guard value")
            write_medians.append(statistics.median(write_times))
            read_medians.append(statistics.median(read_times))
        write_bandwidth, fixed_write = _fit_cost(sample_bytes, (write_medians[0], write_medians[1]))
        read_bandwidth, fixed_read = _fit_cost(sample_bytes, (read_medians[0], read_medians[1]))
        return FactorCacheCalibration(
            root=str(store.resolve()),
            measured_at_ns=time.time_ns(),
            device_id=store.stat().st_dev,
            sample_bytes=sample_bytes,
            repeats=repeats,
            estimated_read_bytes_per_second=read_bandwidth,
            estimated_write_bytes_per_second=write_bandwidth,
            fixed_read_seconds=fixed_read,
            fixed_write_seconds=fixed_write,
            page_cache_eviction_requested=eviction_requested,
        )
    finally:
        shutil.rmtree(calibration_root, ignore_errors=True)


__all__ = [
    "FactorCacheCalibration",
    "FactorCacheFinding",
    "FactorCacheInventory",
    "FactorCacheNodeInventory",
    "calibrate_factor_cache",
    "inspect_factor_cache",
]
