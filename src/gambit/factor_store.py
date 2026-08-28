"""Crash-safe generation publication for experimental mapped factor columns."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import socket
import time
import uuid
from collections.abc import Iterator
from collections.abc import Mapping as MappingABC
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from numpy.typing import NDArray

from gambit.factor_cache import MappedFloat64Column

FORMAT = "gambit-factor-generation"
VERSION = 1
_GENERATION_PATTERN = re.compile(r"[0-9a-f]{32}")
_COLUMN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")
_NODE_KEY_PATTERN = re.compile(r"[0-9a-f]{64}")

__all__ = [
    "FactorGenerationLease",
    "FactorStoreError",
    "collect_garbage",
    "open_current_generation",
    "publish_generation",
]


class FactorStoreError(RuntimeError):
    """Raised when a factor generation cannot be safely published or opened."""


class FactorGenerationLease(MappingABC[str, Any]):
    """Mapping of opened columns that keeps its generation leased until closed."""

    def __init__(self, generation: str, columns: dict[str, Any], lease_path: Path) -> None:
        self.generation = generation
        self._columns = columns
        self._lease_path = lease_path
        self._closed = False

    def __getitem__(self, name: str) -> Any:
        if self._closed:
            raise FactorStoreError("factor generation lease is closed")
        return self._columns[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def close(self) -> None:
        if self._closed:
            return
        self._columns.clear()
        self._lease_path.unlink(missing_ok=True)
        self._closed = True

    def __enter__(self) -> FactorGenerationLease:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _store_lock(store: Path, *, exclusive: bool) -> Iterator[None]:
    store.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(store / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def publish_generation(
    root: str | Path,
    node_key: str,
    columns: Mapping[str, NDArray[np.float64]],
) -> str:
    """Publish one immutable generation and atomically make it current."""
    if MappedFloat64Column is None:
        raise FactorStoreError("native mapped-column support is unavailable")
    if not columns:
        raise ValueError("columns must not be empty")
    if _NODE_KEY_PATTERN.fullmatch(node_key) is None:
        raise ValueError("node_key must be a lowercase SHA-256 digest")
    if any(_COLUMN_PATTERN.fullmatch(name) is None for name in columns):
        raise ValueError("factor column names must be safe portable identifiers")

    store = Path(root)
    generations = store / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    generation = uuid.uuid4().hex
    staging = generations / f".staging-{generation}"
    destination = generations / generation
    pointer_staging = store / f".CURRENT-{generation}"
    staging.mkdir()
    try:
        manifest_columns: dict[str, dict[str, int | str]] = {}
        expected_rows: int | None = None
        for name in sorted(columns):
            path = staging / f"{name}.bin"
            values = np.asarray(columns[name], dtype=np.float64)
            if values.ndim != 1:
                raise ValueError("factor columns must be one-dimensional")
            if expected_rows is None:
                expected_rows = len(values)
            elif len(values) != expected_rows:
                raise ValueError("factor columns must have equal row counts")
            column = MappedFloat64Column.create(str(path), values)
            manifest_columns[name] = {
                "file": path.name,
                "rows": column.row_count,
                "checksum": column.checksum,
            }
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "generation": generation,
            "node_key": node_key,
            "columns": manifest_columns,
        }
        manifest_path = staging / "manifest.json"
        with manifest_path.open("xb") as file:
            file.write(_canonical_json(manifest) + b"\n")
            file.flush()
            os.fsync(file.fileno())
        _fsync_directory(staging)
        with _store_lock(store, exclusive=True):
            staging.rename(destination)
            _fsync_directory(generations)
            with pointer_staging.open("xb") as file:
                file.write(f"{generation}\n".encode())
                file.flush()
                os.fsync(file.fileno())
            os.replace(pointer_staging, store / "CURRENT")
            _fsync_directory(store)
        return generation
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        pointer_staging.unlink(missing_ok=True)
        raise


def open_current_generation(root: str | Path) -> FactorGenerationLease:
    """Open the current committed generation after strict manifest validation."""
    if MappedFloat64Column is None:
        raise FactorStoreError("native mapped-column support is unavailable")
    store = Path(root)
    with _store_lock(store, exclusive=False):
        return _open_and_lease_current(store)


def _open_and_lease_current(store: Path) -> FactorGenerationLease:
    try:
        generation = (store / "CURRENT").read_text().strip()
    except OSError as error:
        raise FactorStoreError("factor store has no readable CURRENT generation") from error
    if _GENERATION_PATTERN.fullmatch(generation) is None:
        raise FactorStoreError("factor store CURRENT generation is invalid")
    generation_path = store / "generations" / generation
    manifest_path = generation_path / "manifest.json"
    if generation_path.is_symlink() or manifest_path.is_symlink():
        raise FactorStoreError("factor generation may not use symbolic links")
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, ValueError) as error:
        raise FactorStoreError("factor generation manifest is unreadable") from error
    if (
        manifest.get("format") != FORMAT
        or manifest.get("version") != VERSION
        or manifest.get("generation") != generation
        or _NODE_KEY_PATTERN.fullmatch(str(manifest.get("node_key", ""))) is None
    ):
        raise FactorStoreError("factor generation manifest identity is invalid")
    columns = manifest.get("columns")
    if not isinstance(columns, dict) or not columns:
        raise FactorStoreError("factor generation manifest has no columns")

    opened: dict[str, Any] = {}
    for name, metadata in columns.items():
        if _COLUMN_PATTERN.fullmatch(name) is None or not isinstance(metadata, dict):
            raise FactorStoreError("factor generation column metadata is invalid")
        expected_file = f"{name}.bin"
        if metadata.get("file") != expected_file:
            raise FactorStoreError("factor generation column filename is invalid")
        column_path = generation_path / expected_file
        if column_path.is_symlink():
            raise FactorStoreError("factor generation may not use symbolic links")
        column = MappedFloat64Column.open(str(column_path))
        if column.row_count != metadata.get("rows") or column.checksum != metadata.get("checksum"):
            raise FactorStoreError("factor generation column metadata mismatch")
        opened[name] = column
    lease_directory = store / "leases" / generation
    lease_directory.mkdir(parents=True, exist_ok=True)
    lease_path = lease_directory / f"{os.getpid()}-{uuid.uuid4().hex}.json"
    lease = {
        "generation": generation,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "created_ns": time.time_ns(),
    }
    with lease_path.open("xb") as file:
        file.write(_canonical_json(lease) + b"\n")
        file.flush()
        os.fsync(file.fileno())
    _fsync_directory(lease_directory)
    return FactorGenerationLease(generation, opened, lease_path)


def _local_process_is_dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, ValueError):
        return False
    return False


def collect_garbage(root: str | Path, *, stale_lease_seconds: float = 86400.0) -> dict[str, list[str]]:
    """Remove invisible unleased generations while failing safe on ambiguous leases."""
    if stale_lease_seconds < 0:
        raise ValueError("stale_lease_seconds must be non-negative")
    store = Path(root)
    removed_generations: list[str] = []
    removed_leases: list[str] = []
    with _store_lock(store, exclusive=True):
        try:
            current = (store / "CURRENT").read_text().strip()
        except OSError as error:
            raise FactorStoreError("factor store has no readable CURRENT generation") from error
        if _GENERATION_PATTERN.fullmatch(current) is None:
            raise FactorStoreError("factor store CURRENT generation is invalid")
        now_ns = time.time_ns()
        leases_root = store / "leases"
        generations = store / "generations"
        for generation_path in generations.iterdir():
            generation = generation_path.name
            if generation == current or _GENERATION_PATTERN.fullmatch(generation) is None:
                continue
            lease_directory = leases_root / generation
            if lease_directory.is_symlink():
                has_leases = True
            elif lease_directory.is_dir():
                for lease_path in lease_directory.iterdir():
                    try:
                        metadata = json.loads(lease_path.read_bytes())
                        age_seconds = (now_ns - int(metadata["created_ns"])) / 1_000_000_000
                        local_dead = metadata["host"] == socket.gethostname() and _local_process_is_dead(
                            int(metadata["pid"])
                        )
                    except (OSError, ValueError, KeyError, TypeError):
                        continue
                    if age_seconds >= stale_lease_seconds and local_dead:
                        lease_path.unlink(missing_ok=True)
                        removed_leases.append(f"{generation}/{lease_path.name}")
                has_leases = any(lease_directory.iterdir())
            else:
                has_leases = False
            if not has_leases and generation_path.is_dir() and not generation_path.is_symlink():
                shutil.rmtree(generation_path)
                removed_generations.append(generation)
                if lease_directory.is_dir():
                    lease_directory.rmdir()
        _fsync_directory(generations)
    return {"removed_generations": removed_generations, "removed_leases": removed_leases}
