"""Allocation/lifetime stress probe for platform leak and sanitizer tools."""

from __future__ import annotations

import ctypes
import gc
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, TypeVar

_Result = TypeVar("_Result")


def main() -> None:
    sanitizer_runtime = ctypes.CDLL(None)
    disable_leak_tracking = getattr(sanitizer_runtime, "__lsan_disable", lambda: None)
    enable_leak_tracking = getattr(sanitizer_runtime, "__lsan_enable", lambda: None)
    disable_leak_tracking()

    import numpy as np

    from gambit import _io
    from gambit.factor_cache import TICK_DTYPE, MappedFloat64Column, TickFactorProcessor, TickRing

    def tracked_call(function: Callable[..., _Result], *args: object) -> _Result:
        enable_leak_tracking()
        try:
            return function(*args)
        finally:
            disable_leak_tracking()

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        csv_path = root / "values.csv"
        csv_path.write_text("value,42\n")
        archive_path = root / "values.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("values.csv", "value,42\n")

        for _ in range(2_000):
            labels, values = tracked_call(_io.read_file, str(csv_path), [0, 1], ["S16", "i8"], ",", 0, 0)
            assert labels[0] == b"value" and values[0] == 42
            try:
                tracked_call(_io.read_file, str(csv_path), [0], ["S0"], ",", 0, 0)
            except TypeError:
                pass
            else:
                raise AssertionError("invalid string width was accepted")

        for _ in range(200):
            labels, values = tracked_call(_io.read_file, f"{archive_path}:values.csv", [0, 1], ["S16", "i8"], ",", 0, 0)
            assert labels[0] == b"value" and values[0] == 42

        if MappedFloat64Column is not None:
            for index in range(200):
                path = root / f"column-{index}.bin"
                create_column = MappedFloat64Column.create_chunked if index % 2 else MappedFloat64Column.create
                column = tracked_call(create_column, str(path), np.arange(128, dtype=np.float64))
                view = column.values
                del column
                assert view[-1] == 127
                del view

        if TickRing is not None and TickFactorProcessor is not None:
            records = np.zeros(128, dtype=TICK_DTYPE)
            records["sequence"] = np.arange(128)
            records["price"] = 100.0
            records["quantity"] = 1.0
            for _ in range(2_000):
                ring = tracked_call(TickRing, 128)
                processor = tracked_call(TickFactorProcessor)
                assert tracked_call(ring.push_batch, records) == 128
                assert tracked_call(ring.process_batch, processor, 128) == 128

    gc.collect()

    # Check while the interpreter and extension modules are still live. CPython
    # intentionally retains allocator arenas and free lists; tearing the
    # interpreter down before LeakSanitizer runs makes those look unreachable.
    leak_check = getattr(sanitizer_runtime, "__lsan_do_recoverable_leak_check", None)
    if leak_check is not None:
        leak_check.restype = ctypes.c_int
        leaks_detected = leak_check()
        os._exit(1 if leaks_detected else 0)


if __name__ == "__main__":
    main()
