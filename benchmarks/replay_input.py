"""Bounded experimental input readers. A retained NumPy view prevents reuse/close."""

from __future__ import annotations

import argparse
import ctypes
import json
import mmap
import os
import subprocess
import sys
import threading
import weakref
from pathlib import Path

import numpy as np
from replay_contract import MAX_CHUNK, digest, read_json

from gambit.tick_backtest import QUEUE_DTYPE

MODES = ("legacy", "fused", "readinto", "mmap")


def build(output, sanitize=False):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).with_name("fused_queue_input.cpp").resolve()
    library = output / ("queue_input.dylib" if sys.platform == "darwin" else "queue_input.so")
    compiler = os.environ.get("CXX", "c++")
    command = [compiler, "-std=c++11", "-O1" if sanitize else "-O3", "-Wall", "-Wextra", "-Werror",
               "-fPIC", "-dynamiclib" if sys.platform == "darwin" else "-shared", str(source), "-o", str(library)]
    if sanitize:
        command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    (output / "compiler.log").write_text(completed.stdout + completed.stderr)
    completed.check_returncode()
    manifest = dict(command=command, compiler_version=subprocess.check_output([compiler, "--version"], text=True),
                    source_sha256=digest(source), library_sha256=digest(library), sanitized=sanitize)
    (output / "build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return library


class InputChunks:
    """One bounded buffer/window, single-owner reads, no overwrite of live views.

    The caller must discard the returned array AND its derived views before the
    next read or close. Read-only flags protect ordinary writes during processing;
    callers must not deliberately bypass them or mutate the backing dataset.
    Mapped datasets must be immutable. External truncation can fault a worker and
    must be contained by the controlled replay's subprocess supervisor.
    """

    def __init__(self, mode, chunk_size, *, dataset=None, size_bytes=None, library=None):
        if mode not in MODES[1:] or type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK:
            raise ValueError("invalid input mode or chunk bound")
        if sys.byteorder != "little":
            raise ValueError("input experiment requires a little-endian host")
        if (mode == "fused") != (dataset is None):
            raise ValueError("fused generation and storage sources cannot be mixed")
        self.mode, self.chunk_size = mode, chunk_size
        self._lock = threading.Lock()
        self._live = lambda: None
        self._mapping = None
        self._stream = None
        self._closed = False
        self._buffer = bytearray(chunk_size * 88) if mode != "mmap" else None
        self.library_identity = None
        if mode == "fused":
            path = Path(library).resolve()
            manifest = read_json(path.parent / "build.json")
            if (manifest["library_sha256"] != digest(path) or
                    manifest["source_sha256"] != digest(Path(__file__).with_name("fused_queue_input.cpp"))):
                raise ValueError("input library/source digest mismatch")
            self.library_identity = manifest
            self._library = ctypes.CDLL(str(path))
            self._fill = self._library.gambit_fill_queue
            self._fill.argtypes = [ctypes.c_void_p] + [ctypes.c_uint64] * 4
            self._fill.restype = ctypes.c_int
        else:
            self._stream = (Path(dataset) / "events.bin").open("rb", buffering=0)
            self._size = size_bytes
            if type(size_bytes) is not int or os.fstat(self._stream.fileno()).st_size != size_bytes:
                self._stream.close()
                raise ValueError("opened dataset length mismatch")

    def _available(self):
        if self._closed:
            raise ValueError("input reader is closed")
        if self._live() is not None:
            raise BufferError("discard the previous array and all derived views before reuse/close")
        if self._mapping is not None:
            self._mapping.close()
            self._mapping = None

    def read(self, offset, count, seed=20260904):
        if (type(offset) is not int or offset < 0 or type(count) is not int or not 1 <= count <= self.chunk_size or
                type(seed) is not int or not 0 <= seed < 2**64):
            raise ValueError("invalid input extent or seed")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("concurrent input access")
        try:
            self._available()
            if self.mode == "fused":
                if offset + count - 1 > ((2**63 - 1) - 1_000_000) // 100_000_000:
                    raise ValueError("input timestamp overflow")
                address = ctypes.addressof(ctypes.c_char.from_buffer(self._buffer))
                if self._fill(address, len(self._buffer), offset, count, seed):
                    raise ValueError("native input extent rejected")
                array = np.ndarray(count, dtype=QUEUE_DTYPE, buffer=self._buffer)
            else:
                if (offset + count) * 88 > self._size or os.fstat(self._stream.fileno()).st_size != self._size:
                    raise ValueError("dataset truncated or extent exceeds manifest")
                begin, length = offset * 88, count * 88
                if self.mode == "readinto":
                    self._stream.seek(begin)
                    target = memoryview(self._buffer)[:length]
                    filled = 0
                    while filled < length:
                        received = self._stream.readinto(target[filled:])
                        if not received:
                            raise ValueError("truncated dataset during read")
                        filled += received
                    array = np.ndarray(count, dtype=QUEUE_DTYPE, buffer=self._buffer)
                else:
                    aligned = begin - begin % mmap.ALLOCATIONGRANULARITY
                    self._mapping = mmap.mmap(self._stream.fileno(), length + begin - aligned,
                                              access=mmap.ACCESS_READ, offset=aligned)
                    array = np.ndarray(count, dtype=QUEUE_DTYPE, buffer=self._mapping, offset=begin - aligned)
            array.setflags(write=False)
            self._live = weakref.ref(array)
            return array
        finally:
            self._lock.release()

    def close(self):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("concurrent input access")
        try:
            if self._closed:
                return
            self._available()
            valid_size = self._stream is None or os.fstat(self._stream.fileno()).st_size == self._size
            if self._stream is not None:
                self._stream.close()
            self._closed = True
            if not valid_size:
                raise ValueError("dataset length changed during replay")
        finally:
            self._lock.release()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    print(build(args.output, args.sanitize))
