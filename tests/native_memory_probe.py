"""Allocation/lifetime stress probe for platform leak and sanitizer tools."""

from __future__ import annotations

import argparse
import ctypes
import gc
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, TypeVar

_Result = TypeVar("_Result")


def exercise(tracked_call: Callable, iterations: int = 2_000) -> None:
    """Return from the workload before asking LSan to inspect released results."""
    import numpy as np

    from gambit import _io
    from gambit.factor_cache import TICK_DTYPE, MappedFloat64Column, TickFactorProcessor, TickRing

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        csv_path = root / "values.csv"
        csv_path.write_text("value,42\n")
        archive_path = root / "values.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("values.csv", "value,42\n")

        for _ in range(iterations):
            labels, values = tracked_call(_io.read_file, str(csv_path), [0, 1], ["S16", "i8"], ",", 0, 0)
            assert labels[0] == b"value" and values[0] == 42
            try:
                tracked_call(_io.read_file, str(csv_path), [0], ["S0"], ",", 0, 0)
            except TypeError:
                pass
            else:
                raise AssertionError("invalid string width was accepted")

        for _ in range(max(1, iterations // 10)):
            labels, values = tracked_call(_io.read_file, f"{archive_path}:values.csv", [0, 1], ["S16", "i8"], ",", 0, 0)
            assert labels[0] == b"value" and values[0] == 42
            for source in (str(csv_path), f"{archive_path}:values.csv"):
                try:
                    tracked_call(lambda: _io.read_file(source, [0, 1], ["S16", "i8"], skip_rows=0,
                                                       max_output_bytes=8))
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("output budget failure was accepted")
            try:
                tracked_call(_io.read_file, f"{archive_path}:missing.csv", [0], ["i8"], ",", 0, 0)
            except RuntimeError:
                pass
            else:
                raise AssertionError("missing ZIP member was accepted")

        if MappedFloat64Column is not None:
            for index in range(max(1, iterations // 10)):
                path = root / f"column-{index}.bin"
                create_column = MappedFloat64Column.create_chunked_v3 if index % 2 else MappedFloat64Column.create
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
            for _ in range(iterations):
                ring = tracked_call(TickRing, 128)
                processor = tracked_call(TickFactorProcessor)
                assert tracked_call(ring.push_batch, records) == 128
                assert tracked_call(ring.process_batch, processor, 128) == 128
                assert tracked_call(ring.push_batch, records) == 128
                lease = tracked_call(ring.lease_batch, 128)
                view = tracked_call(lambda: lease.values)
                lease.close()
                del view
                del lease

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=2_000)
    parser.add_argument("--require-lsan", action="store_true")
    args = parser.parse_args()
    if args.iterations <= 0:
        parser.error("iterations must be positive")
    sanitizer_runtime = ctypes.CDLL(None)
    disable_leak_tracking = getattr(sanitizer_runtime, "__lsan_disable", None)
    enable_leak_tracking = getattr(sanitizer_runtime, "__lsan_enable", None)
    leak_check = getattr(sanitizer_runtime, "__lsan_do_recoverable_leak_check", None)
    if args.require_lsan and not all((disable_leak_tracking, enable_leak_tracking, leak_check)):
        parser.error("--require-lsan needs a loaded LeakSanitizer runtime")
    disable_leak_tracking = disable_leak_tracking or (lambda: None)
    enable_leak_tracking = enable_leak_tracking or (lambda: None)
    disable_leak_tracking()

    def tracked_call(function: Callable[..., _Result], *args: object) -> _Result:
        enable_leak_tracking()
        try:
            return function(*args)
        finally:
            disable_leak_tracking()

    exercise(tracked_call, args.iterations)
    gc.collect()

    # Check while the interpreter and extension modules are still live. CPython
    # intentionally retains allocator arenas and free lists; tearing the
    # interpreter down before LeakSanitizer runs makes those look unreachable.
    if leak_check is not None:
        leak_check.restype = ctypes.c_int
        leaks_detected = leak_check()
        print(f"native lifetime workload released; LeakSanitizer result={leaks_detected}", flush=True)
        os._exit(1 if leaks_detected else 0)
    print("native lifetime workload released; no LeakSanitizer runtime (ordinary smoke only)")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
