"""Durable, advisory rejection metadata for factor-cache admission."""

from __future__ import annotations

import json
import math
import os
import re
import time
import uuid
from pathlib import Path

FORMAT = "gambit-factor-admission"
VERSION = 1
_NODE_KEY_PATTERN = re.compile(r"[0-9a-f]{64}")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def has_recent_rejection(
    root: str | Path,
    node_key: str,
    policy_key: str,
    *,
    ttl_seconds: float,
) -> bool:
    """Return whether a strictly valid rejection hint is still fresh."""
    if _NODE_KEY_PATTERN.fullmatch(node_key) is None or _NODE_KEY_PATTERN.fullmatch(policy_key) is None:
        raise ValueError("node_key and policy_key must be lowercase SHA-256 digests")
    if not math.isfinite(ttl_seconds) or ttl_seconds < 0:
        raise ValueError("ttl_seconds must be finite and non-negative")
    path = Path(root) / "admission" / f"{node_key}.json"
    if path.is_symlink():
        return False
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return False
    if not isinstance(value, dict) or set(value) != {
        "format",
        "version",
        "node_key",
        "policy_key",
        "decision",
        "compute_seconds",
        "output_bytes",
        "recorded_ns",
    }:
        return False
    if (
        value["format"] != FORMAT
        or type(value["version"]) is not int
        or value["version"] != VERSION
        or value["node_key"] != node_key
        or value["policy_key"] != policy_key
        or value["decision"] != "decline"
        or not isinstance(value["compute_seconds"], (int, float))
        or isinstance(value["compute_seconds"], bool)
        or not math.isfinite(value["compute_seconds"])
        or value["compute_seconds"] < 0
        or type(value["output_bytes"]) is not int
        or value["output_bytes"] < 0
        or type(value["recorded_ns"]) is not int
        or value["recorded_ns"] < 0
    ):
        return False
    age_ns = time.time_ns() - value["recorded_ns"]
    return 0 <= age_ns < ttl_seconds * 1_000_000_000


def record_rejection(
    root: str | Path,
    node_key: str,
    policy_key: str,
    *,
    compute_seconds: float,
    output_bytes: int,
) -> None:
    """Atomically persist a replaceable optimization hint."""
    if _NODE_KEY_PATTERN.fullmatch(node_key) is None or _NODE_KEY_PATTERN.fullmatch(policy_key) is None:
        raise ValueError("node_key and policy_key must be lowercase SHA-256 digests")
    if not math.isfinite(compute_seconds) or compute_seconds < 0 or output_bytes < 0:
        raise ValueError("admission measurements must be finite and non-negative")
    directory = Path(root) / "admission"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{node_key}.json"
    staging = directory / f".{node_key}-{uuid.uuid4().hex}"
    value = {
        "format": FORMAT,
        "version": VERSION,
        "node_key": node_key,
        "policy_key": policy_key,
        "decision": "decline",
        "compute_seconds": compute_seconds,
        "output_bytes": output_bytes,
        "recorded_ns": time.time_ns(),
    }
    try:
        with staging.open("xb") as file:
            file.write(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(staging, destination)
        _fsync_directory(directory)
    finally:
        staging.unlink(missing_ok=True)


def clear_rejection(root: str | Path, node_key: str) -> None:
    """Remove a rejection after the node is successfully admitted."""
    if _NODE_KEY_PATTERN.fullmatch(node_key) is None:
        raise ValueError("node_key must be a lowercase SHA-256 digest")
    directory = Path(root) / "admission"
    path = directory / f"{node_key}.json"
    if path.is_file() and not path.is_symlink():
        path.unlink()
        _fsync_directory(directory)


__all__ = ["clear_rejection", "has_recent_rejection", "record_rejection"]
