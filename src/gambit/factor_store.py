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
from gambit.factor_identity import FactorNodeIdentity

FORMAT = "gambit-factor-generation"
VERSION = 1
_GENERATION_PATTERN = re.compile(r"[0-9a-f]{32}")
_COLUMN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")
_NODE_KEY_PATTERN = re.compile(r"[0-9a-f]{64}")

__all__ = [
    "FactorGenerationLease",
    "FactorNodeCacheMiss",
    "FactorStoreError",
    "collect_garbage",
    "evict_factor_nodes",
    "open_current_generation",
    "open_generation_by_node_key",
    "publish_factor_node",
    "publish_generation",
]


class FactorStoreError(RuntimeError):
    """Raised when a factor generation cannot be safely published or opened."""


class FactorNodeCacheMiss(FactorStoreError):
    """Raised only when a valid node key has no cache-index entry."""


class FactorGenerationLease(MappingABC[str, Any]):
    """Mapping of opened columns that keeps its generation leased until closed."""

    def __init__(
        self,
        generation: str,
        node_key: str,
        identity: FactorNodeIdentity | None,
        columns: dict[str, Any],
        lease_path: Path,
    ) -> None:
        self.generation = generation
        self.node_key = node_key
        self.identity = identity
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


@contextmanager
def _writer_lock(store: Path) -> Iterator[None]:
    """Serialize generation construction with orphan reclamation."""
    store.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(store / ".writer.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def publish_generation(
    root: str | Path,
    node_key: str,
    columns: Mapping[str, NDArray[np.float64]],
    *,
    identity: FactorNodeIdentity | None = None,
) -> str:
    """Publish one immutable generation and atomically make it current."""
    if MappedFloat64Column is None:
        raise FactorStoreError("native mapped-column support is unavailable")
    if not columns:
        raise ValueError("columns must not be empty")
    if _NODE_KEY_PATTERN.fullmatch(node_key) is None:
        raise ValueError("node_key must be a lowercase SHA-256 digest")
    if identity is not None and identity.node_key != node_key:
        raise ValueError("identity does not hash to node_key")
    if any(_COLUMN_PATTERN.fullmatch(name) is None for name in columns):
        raise ValueError("factor column names must be safe portable identifiers")

    store = Path(root)
    with _writer_lock(store):
        if identity is not None:
            indexed_generation = _read_node_pointer(store, node_key, missing_ok=True)
            if indexed_generation is not None:
                with _store_lock(store, exclusive=False):
                    lease = _open_and_lease_generation(
                        store,
                        indexed_generation,
                        expected_node_key=node_key,
                        require_identity=True,
                    )
                    _record_node_access(
                        store,
                        node_key,
                        indexed_generation,
                        minimum_interval_seconds=60,
                    )
                lease.close()
                return indexed_generation
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
                column = MappedFloat64Column.create_chunked(str(path), values)
                manifest_columns[name] = {
                    "file": path.name,
                    "rows": column.row_count,
                    "checksum": column.checksum,
                    "segment_version": column.format_version,
                }
            manifest = {
                "format": FORMAT,
                "version": VERSION,
                "generation": generation,
                "node_key": node_key,
                "created_ns": time.time_ns(),
                "columns": manifest_columns,
            }
            if identity is not None:
                manifest["identity"] = identity.snapshot()
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
                if identity is not None:
                    nodes = store / "nodes"
                    nodes.mkdir(exist_ok=True)
                    node_staging = nodes / f".{node_key}-{generation}"
                    with node_staging.open("xb") as file:
                        file.write(f"{generation}\n".encode())
                        file.flush()
                        os.fsync(file.fileno())
                    os.replace(node_staging, nodes / node_key)
                    _fsync_directory(nodes)
                    _record_node_access(store, node_key, generation, minimum_interval_seconds=0)
            return generation
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            pointer_staging.unlink(missing_ok=True)
            raise


def publish_factor_node(
    root: str | Path,
    identity: FactorNodeIdentity,
    columns: Mapping[str, NDArray[np.float64]],
) -> str:
    """Publish a strict factor identity, reusing an existing valid generation."""
    schema = {column.name: column for column in identity.output_schema}
    if set(schema) != set(columns):
        raise ValueError("identity output schema must exactly match published columns")
    if any(column.dtype != "float64" or column.nullable for column in schema.values()):
        raise ValueError("mapped factor storage currently requires non-nullable float64 columns")
    return publish_generation(root, identity.node_key, columns, identity=identity)


def _read_node_pointer(store: Path, node_key: str, *, missing_ok: bool) -> str | None:
    if _NODE_KEY_PATTERN.fullmatch(node_key) is None:
        raise ValueError("node_key must be a lowercase SHA-256 digest")
    pointer = store / "nodes" / node_key
    if pointer.is_symlink():
        raise FactorStoreError("factor node index may not use symbolic links")
    try:
        generation = pointer.read_text().strip()
    except FileNotFoundError:
        if missing_ok:
            return None
        raise FactorNodeCacheMiss("factor node is not cached") from None
    except OSError as error:
        raise FactorStoreError("factor node index is unreadable") from error
    if _GENERATION_PATTERN.fullmatch(generation) is None:
        raise FactorStoreError("factor node index generation is invalid")
    return generation


def _record_node_access(
    store: Path,
    node_key: str,
    generation: str,
    *,
    minimum_interval_seconds: float,
) -> None:
    """Best-effort rate-limited LRU metadata; never part of correctness."""
    directory = store / "access"
    if directory.is_symlink():
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{node_key}.json"
        now_ns = time.time_ns()
        access_count = 0
        if destination.is_file() and not destination.is_symlink():
            try:
                previous = json.loads(destination.read_bytes())
                if (
                    isinstance(previous, dict)
                    and previous.get("node_key") == node_key
                    and previous.get("generation") == generation
                    and type(previous.get("last_access_ns")) is int
                    and type(previous.get("access_count")) is int
                ):
                    last_access_ns = int(previous["last_access_ns"])
                    access_count = max(0, int(previous["access_count"]))
                    if 0 <= last_access_ns <= now_ns and (
                        now_ns - last_access_ns < minimum_interval_seconds * 1_000_000_000
                    ):
                        return
            except (OSError, ValueError, TypeError):
                pass
        value = {
            "format": "gambit-factor-access",
            "version": 1,
            "node_key": node_key,
            "generation": generation,
            "last_access_ns": now_ns,
            "access_count": access_count + 1,
        }
        staging = directory / f".{node_key}-{uuid.uuid4().hex}"
        try:
            with staging.open("xb") as file:
                file.write(_canonical_json(value) + b"\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(staging, destination)
            _fsync_directory(directory)
        finally:
            staging.unlink(missing_ok=True)
    except OSError:
        return


def _read_node_last_access(store: Path, node_key: str, generation: str, fallback_ns: int) -> int:
    path = store / "access" / f"{node_key}.json"
    if path.is_symlink():
        return fallback_ns
    try:
        value = json.loads(path.read_bytes())
        if (
            not isinstance(value, dict)
            or value.get("format") != "gambit-factor-access"
            or value.get("version") != 1
            or value.get("node_key") != node_key
            or value.get("generation") != generation
            or type(value.get("last_access_ns")) is not int
        ):
            return fallback_ns
        last_access_ns = int(value["last_access_ns"])
        return last_access_ns if 0 <= last_access_ns <= time.time_ns() else fallback_ns
    except (OSError, ValueError, TypeError):
        return fallback_ns


def _generation_stored_bytes(path: Path) -> int:
    total = 0
    for entry in path.iterdir():
        if entry.is_symlink():
            raise FactorStoreError("factor generation may not use symbolic links")
        if entry.is_file():
            total += entry.stat().st_size
    return total


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
    return _open_and_lease_generation(store, generation)


def open_generation_by_node_key(
    root: str | Path,
    node_key: str,
    *,
    access_update_interval_seconds: float = 60.0,
) -> FactorGenerationLease:
    """Open and lease a cached generation using its deterministic factor-node key."""
    if MappedFloat64Column is None:
        raise FactorStoreError("native mapped-column support is unavailable")
    if not np.isfinite(access_update_interval_seconds) or access_update_interval_seconds < 0:
        raise ValueError("access_update_interval_seconds must be finite and non-negative")
    store = Path(root)
    with _store_lock(store, exclusive=False):
        generation = _read_node_pointer(store, node_key, missing_ok=False)
        assert generation is not None
        lease = _open_and_lease_generation(
            store,
            generation,
            expected_node_key=node_key,
            require_identity=True,
        )
        _record_node_access(
            store,
            node_key,
            generation,
            minimum_interval_seconds=access_update_interval_seconds,
        )
        return lease


def _open_and_lease_generation(
    store: Path,
    generation: str,
    *,
    expected_node_key: str | None = None,
    require_identity: bool = False,
) -> FactorGenerationLease:
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
    node_key = str(manifest["node_key"])
    if expected_node_key is not None and node_key != expected_node_key:
        raise FactorStoreError("factor generation node key does not match its index")
    identity_snapshot = manifest.get("identity")
    identity: FactorNodeIdentity | None = None
    if identity_snapshot is not None:
        try:
            identity = FactorNodeIdentity.from_snapshot(identity_snapshot)
        except (TypeError, ValueError) as error:
            raise FactorStoreError("factor generation identity snapshot is invalid") from error
        if identity.node_key != node_key:
            raise FactorStoreError("factor generation identity hash mismatch")
    elif require_identity:
        raise FactorStoreError("factor generation has no strict identity snapshot")
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
        if (
            column.row_count != metadata.get("rows")
            or column.checksum != metadata.get("checksum")
            or column.format_version != metadata.get("segment_version")
        ):
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
    return FactorGenerationLease(generation, node_key, identity, opened, lease_path)


def _local_process_is_dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, ValueError):
        return False
    return False


def collect_garbage(
    root: str | Path,
    *,
    stale_lease_seconds: float = 86400.0,
    metadata_retention_seconds: float = 30 * 86400.0,
    dry_run: bool = False,
) -> dict[str, object]:
    """Remove invisible unleased generations while failing safe on ambiguous leases."""
    if not np.isfinite(stale_lease_seconds) or stale_lease_seconds < 0:
        raise ValueError("stale_lease_seconds must be finite and non-negative")
    if not np.isfinite(metadata_retention_seconds) or metadata_retention_seconds < 0:
        raise ValueError("metadata_retention_seconds must be finite and non-negative")
    store = Path(root)
    removed_generations: list[str] = []
    removed_leases: list[str] = []
    removed_staging: list[str] = []
    removed_pointers: list[str] = []
    removed_node_staging: list[str] = []
    removed_metadata: list[str] = []
    with _writer_lock(store):
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
            indexed_generations: set[str] = set()
            indexed_node_keys: set[str] = set()
            nodes = store / "nodes"
            if nodes.is_symlink():
                raise FactorStoreError("factor node index may not use symbolic links")
            if nodes.is_dir():
                for node_pointer in nodes.iterdir():
                    if node_pointer.name.startswith("."):
                        if node_pointer.is_file() and not node_pointer.is_symlink():
                            removed_node_staging.append(node_pointer.name)
                            if not dry_run:
                                node_pointer.unlink()
                        continue
                    if _NODE_KEY_PATTERN.fullmatch(node_pointer.name) is None:
                        raise FactorStoreError("factor node index contains an invalid key")
                    indexed_node_keys.add(node_pointer.name)
                    generation = _read_node_pointer(store, node_pointer.name, missing_ok=False)
                    assert generation is not None
                    generation_path = generations / generation
                    if not generation_path.is_dir() or generation_path.is_symlink():
                        raise FactorStoreError("factor node index references a missing generation")
                    indexed_generations.add(generation)
            for generation_path in generations.iterdir():
                generation = generation_path.name
                if generation.startswith(".staging-"):
                    if generation_path.is_dir() and not generation_path.is_symlink():
                        removed_staging.append(generation)
                        if not dry_run:
                            shutil.rmtree(generation_path)
                    continue
                if (
                    generation == current
                    or generation in indexed_generations
                    or _GENERATION_PATTERN.fullmatch(generation) is None
                ):
                    continue
                lease_directory = leases_root / generation
                if lease_directory.is_symlink():
                    has_leases = True
                elif lease_directory.is_dir():
                    surviving_leases = 0
                    for lease_path in lease_directory.iterdir():
                        try:
                            metadata = json.loads(lease_path.read_bytes())
                            age_seconds = (now_ns - int(metadata["created_ns"])) / 1_000_000_000
                            local_dead = metadata["host"] == socket.gethostname() and _local_process_is_dead(
                                int(metadata["pid"])
                            )
                        except (OSError, ValueError, KeyError, TypeError):
                            surviving_leases += 1
                            continue
                        if age_seconds >= stale_lease_seconds and local_dead:
                            removed_leases.append(f"{generation}/{lease_path.name}")
                            if not dry_run:
                                lease_path.unlink(missing_ok=True)
                        else:
                            surviving_leases += 1
                    has_leases = surviving_leases > 0
                else:
                    has_leases = False
                if not has_leases and generation_path.is_dir() and not generation_path.is_symlink():
                    removed_generations.append(generation)
                    if not dry_run:
                        shutil.rmtree(generation_path)
                        if lease_directory.is_dir():
                            lease_directory.rmdir()
            for pointer_path in store.glob(".CURRENT-*"):
                if pointer_path.is_file() and not pointer_path.is_symlink():
                    removed_pointers.append(pointer_path.name)
                    if not dry_run:
                        pointer_path.unlink()
            metadata_cutoff_ns = now_ns - int(metadata_retention_seconds * 1_000_000_000)
            for directory_name in ("access", "admission"):
                metadata_root = store / directory_name
                if metadata_root.is_symlink():
                    raise FactorStoreError("factor metadata directories may not use symbolic links")
                if not metadata_root.is_dir():
                    continue
                for metadata_path in metadata_root.iterdir():
                    match = re.fullmatch(r"([0-9a-f]{64})\.json", metadata_path.name)
                    if (
                        match is None
                        or match.group(1) in indexed_node_keys
                        or metadata_path.is_symlink()
                        or not metadata_path.is_file()
                    ):
                        continue
                    try:
                        old_enough = metadata_path.stat().st_mtime_ns <= metadata_cutoff_ns
                    except OSError:
                        continue
                    if old_enough:
                        removed_metadata.append(f"{directory_name}/{metadata_path.name}")
                        if not dry_run:
                            metadata_path.unlink(missing_ok=True)
            if not dry_run:
                _fsync_directory(generations)
                if removed_pointers:
                    _fsync_directory(store)
                if removed_node_staging:
                    _fsync_directory(nodes)
                for directory_name in ("access", "admission"):
                    metadata_root = store / directory_name
                    if any(path.startswith(f"{directory_name}/") for path in removed_metadata):
                        _fsync_directory(metadata_root)
    return {
        "removed_generations": removed_generations,
        "removed_leases": removed_leases,
        "removed_staging": removed_staging,
        "removed_pointers": removed_pointers,
        "removed_node_staging": removed_node_staging,
        "removed_metadata": removed_metadata,
        "dry_run": dry_run,
    }


def evict_factor_nodes(
    root: str | Path,
    *,
    max_bytes: int,
    max_nodes: int | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Evict least-recently-used indexed nodes without deleting current or leased data."""
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    if max_nodes is not None and (type(max_nodes) is not int or max_nodes < 0):
        raise ValueError("max_nodes must be a non-negative integer or None")
    store = Path(root)
    evicted_node_keys: list[str] = []
    removed_generations: list[str] = []
    protected_node_keys: list[str] = []
    with _writer_lock(store):
        with _store_lock(store, exclusive=True):
            try:
                current = (store / "CURRENT").read_text().strip()
            except OSError as error:
                raise FactorStoreError("factor store has no readable CURRENT generation") from error
            if _GENERATION_PATTERN.fullmatch(current) is None:
                raise FactorStoreError("factor store CURRENT generation is invalid")
            nodes = store / "nodes"
            generations = store / "generations"
            if nodes.is_symlink():
                raise FactorStoreError("factor node index may not use symbolic links")
            entries: list[tuple[int, str, str, int, bool]] = []
            if nodes.is_dir():
                for pointer in nodes.iterdir():
                    node_key = pointer.name
                    if node_key.startswith("."):
                        continue
                    if _NODE_KEY_PATTERN.fullmatch(node_key) is None:
                        raise FactorStoreError("factor node index contains an invalid key")
                    generation = _read_node_pointer(store, node_key, missing_ok=False)
                    assert generation is not None
                    generation_path = generations / generation
                    manifest_path = generation_path / "manifest.json"
                    if generation_path.is_symlink() or manifest_path.is_symlink():
                        raise FactorStoreError("factor generation may not use symbolic links")
                    try:
                        manifest = json.loads(manifest_path.read_bytes())
                    except (OSError, ValueError) as error:
                        raise FactorStoreError("factor generation manifest is unreadable") from error
                    if manifest.get("generation") != generation or manifest.get("node_key") != node_key:
                        raise FactorStoreError("factor node index does not match its generation manifest")
                    stored_bytes = _generation_stored_bytes(generation_path)
                    created_ns = manifest.get("created_ns", manifest_path.stat().st_mtime_ns)
                    if type(created_ns) is not int or created_ns < 0:
                        raise FactorStoreError("factor generation creation timestamp is invalid")
                    fallback_ns = created_ns
                    last_access_ns = _read_node_last_access(store, node_key, generation, fallback_ns)
                    lease_directory = store / "leases" / generation
                    leased = lease_directory.is_symlink() or (
                        lease_directory.is_dir() and any(lease_directory.iterdir())
                    )
                    protected = generation == current or leased
                    entries.append((last_access_ns, node_key, generation, stored_bytes, protected))

            total_bytes_before = sum(entry[3] for entry in entries)
            total_nodes_before = len(entries)
            total_bytes_after = total_bytes_before
            total_nodes_after = total_nodes_before

            def over_limit() -> bool:
                return total_bytes_after > max_bytes or (
                    max_nodes is not None and total_nodes_after > max_nodes
                )

            for _, node_key, generation, stored_bytes, protected in sorted(entries):
                if not over_limit():
                    break
                if protected:
                    protected_node_keys.append(node_key)
                    continue
                evicted_node_keys.append(node_key)
                total_nodes_after -= 1
                generation_path = generations / generation
                removed_generations.append(generation)
                total_bytes_after -= stored_bytes
                if not dry_run:
                    (nodes / node_key).unlink()
                    shutil.rmtree(generation_path)
                    lease_directory = store / "leases" / generation
                    if lease_directory.is_dir() and not any(lease_directory.iterdir()):
                        lease_directory.rmdir()
                    for metadata_root, suffix in (
                        (store / "access", ".json"),
                        (store / "admission", ".json"),
                    ):
                        metadata_path = metadata_root / f"{node_key}{suffix}"
                        if metadata_path.is_file() and not metadata_path.is_symlink():
                            metadata_path.unlink()

            if evicted_node_keys and not dry_run:
                _fsync_directory(nodes)
                _fsync_directory(generations)
                for metadata_root in (store / "access", store / "admission"):
                    if metadata_root.is_dir() and not metadata_root.is_symlink():
                        _fsync_directory(metadata_root)
    return {
        "evicted_node_keys": evicted_node_keys,
        "removed_generations": removed_generations,
        "protected_node_keys": protected_node_keys,
        "total_bytes_before": total_bytes_before,
        "total_bytes_after": total_bytes_after,
        "total_nodes_before": total_nodes_before,
        "total_nodes_after": total_nodes_after,
        "limits_satisfied": not over_limit(),
        "dry_run": dry_run,
    }
