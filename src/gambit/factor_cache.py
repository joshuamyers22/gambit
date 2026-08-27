"""Reference and native primitives for immutable mapped factor columns."""

from __future__ import annotations

import hashlib
import mmap
import struct
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

try:
    from gambit._factor_cache import MappedFloat64Column, TickFactorProcessor, TickRing
except ImportError:  # pragma: no cover - supported fallback on platforms without the native extension
    MappedFloat64Column = None
    TickRing = None
    TickFactorProcessor = None

MAGIC = b"GAMBITFC"
VERSION = 1
COMMITTED = 1
HEADER_BYTES = 4096
HEADER = struct.Struct("<8sIIQQQQ")
TICK_DTYPE = np.dtype(
    [
        ("sequence", "<u8"),
        ("event_time_ns", "<i8"),
        ("receive_time_ns", "<i8"),
        ("price", "<f8"),
        ("quantity", "<f8"),
        ("bid", "<f8"),
        ("ask", "<f8"),
        ("instrument_id", "<u4"),
        ("flags", "<u4"),
    ],
    align=True,
)


def factor_node_key(*, parents: tuple[str, ...], transform: str, parameters: str, input_fingerprint: str) -> str:
    """Build a stable content key for an initial fixed-width factor node."""
    payload = "\x1f".join((*parents, transform, parameters, input_fingerprint, "float64-le-v1"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _checksum(data: memoryview) -> int:
    value = 1469598103934665603
    for byte in data.cast("B"):
        value ^= byte
        value = value * 1099511628211 & ((1 << 64) - 1)
    return value


def write_reference_float64(path: str | Path, values: NDArray[np.float64]) -> None:
    """Correctness oracle implementing the native segment format."""
    array = np.ascontiguousarray(values, dtype="<f8")
    target = Path(path)
    with target.open("xb+") as file:
        file.truncate(HEADER_BYTES + array.nbytes)
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_WRITE) as mapping:
            mapping[:HEADER_BYTES] = bytes(HEADER_BYTES)
            mapping[HEADER_BYTES:] = array.tobytes()
            checksum = _checksum(memoryview(mapping)[HEADER_BYTES:])
            mapping[: HEADER.size] = HEADER.pack(
                MAGIC, VERSION, 0, len(array), HEADER_BYTES, array.nbytes, checksum
            )
            mapping.flush()
            mapping[: HEADER.size] = HEADER.pack(
                MAGIC, VERSION, COMMITTED, len(array), HEADER_BYTES, array.nbytes, checksum
            )
            mapping.flush()


def read_reference_float64(path: str | Path) -> NDArray[np.float64]:
    with Path(path).open("rb") as file:
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as mapping:
            if len(mapping) < HEADER_BYTES:
                raise ValueError("factor cache segment is truncated")
            magic, version, state, rows, offset, data_bytes, checksum = HEADER.unpack(mapping[: HEADER.size])
            if magic != MAGIC or version != VERSION or state != COMMITTED:
                raise ValueError("factor cache header is invalid or uncommitted")
            if offset != HEADER_BYTES or data_bytes != rows * 8 or offset + data_bytes != len(mapping):
                raise ValueError("factor cache segment bounds are invalid")
            data = memoryview(mapping)[offset : offset + data_bytes]
            if _checksum(data) != checksum:
                raise ValueError("factor cache checksum mismatch")
            result: NDArray[np.float64] = np.frombuffer(data, dtype="<f8").copy()
            data.release()
            return result
