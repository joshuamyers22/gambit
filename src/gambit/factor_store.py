"""Crash-safe generation publication for experimental mapped factor columns."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
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

__all__ = ["FactorStoreError", "open_current_generation", "publish_generation"]


class FactorStoreError(RuntimeError):
    """Raised when a factor generation cannot be safely published or opened."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
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


def open_current_generation(root: str | Path) -> dict[str, object]:
    """Open the current committed generation after strict manifest validation."""
    if MappedFloat64Column is None:
        raise FactorStoreError("native mapped-column support is unavailable")
    store = Path(root)
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
    return opened
