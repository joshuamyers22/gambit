"""Allocation/lifetime stress probe for platform leak and sanitizer tools."""

from __future__ import annotations

import gc
import tempfile
import zipfile
from pathlib import Path

import numpy as np

from gambit import _io
from gambit.factor_cache import TICK_DTYPE, MappedFloat64Column, TickFactorProcessor, TickRing


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        csv_path = root / "values.csv"
        csv_path.write_text("value,42\n")
        archive_path = root / "values.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("values.csv", "value,42\n")

        for _ in range(2_000):
            labels, values = _io.read_file(str(csv_path), [0, 1], ["S16", "i8"], ",", 0, 0)
            assert labels[0] == b"value" and values[0] == 42
            try:
                _io.read_file(str(csv_path), [0], ["S0"], ",", 0, 0)
            except TypeError:
                pass
            else:
                raise AssertionError("invalid string width was accepted")

        for _ in range(200):
            labels, values = _io.read_file(
                f"{archive_path}:values.csv", [0, 1], ["S16", "i8"], ",", 0, 0
            )
            assert labels[0] == b"value" and values[0] == 42

        if MappedFloat64Column is not None:
            for index in range(200):
                path = root / f"column-{index}.bin"
                column = MappedFloat64Column.create(str(path), np.arange(128, dtype=np.float64))
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
                ring = TickRing(128)
                processor = TickFactorProcessor()
                assert ring.push_batch(records) == 128
                assert ring.process_batch(processor, 128) == 128

    gc.collect()


if __name__ == "__main__":
    main()
