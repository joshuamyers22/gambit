"""Bounded deterministic fuzz smoke probe for persistence input boundaries."""

from __future__ import annotations

import json
import random
import tempfile
import zipfile
from pathlib import Path

import h5py
import numpy as np

from gambit import _io
from gambit.pq_io import hdf5_to_np_arrays, np_arrays_to_hdf5

CONFIG = Path(__file__).parent / "corpus" / "boundary_fuzz_seeds.json"
CSV_ALPHABET = b"ABCDEF0123456789,;\t\r\n\x00\x7f\xff"


def _exercise_reader(filename: str) -> None:
    try:
        _io.read_file(filename, [0, 1], ["S32", "f8"], ",", 0, 256)
    except (RuntimeError, TypeError, ValueError, OverflowError):
        pass


def _fuzz_csv(root: Path, rng: random.Random, count: int, max_bytes: int) -> None:
    for case in range(count):
        size = rng.randrange(max_bytes + 1)
        payload = bytes(rng.choice(CSV_ALPHABET) for _ in range(size))
        path = root / f"csv-{case}.csv"
        path.write_bytes(payload)
        _exercise_reader(str(path))


def _fuzz_zip(root: Path, rng: random.Random, count: int) -> None:
    for case in range(count):
        path = root / f"zip-{case}.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("data.csv", f"ES,{rng.random() * 10_000}\n" * rng.randrange(1, 8))
        payload = bytearray(path.read_bytes())
        for _ in range(rng.randrange(1, 9)):
            if payload:
                offset = rng.randrange(len(payload))
                payload[offset] ^= 1 << rng.randrange(8)
        if payload and rng.randrange(3) == 0:
            del payload[-rng.randrange(1, min(32, len(payload)) + 1) :]
        path.write_bytes(payload)
        _exercise_reader(f"{path}:data.csv")


def _fuzz_hdf5(root: Path, rng: random.Random, count: int) -> None:
    mutations = ("format", "version", "state", "rows", "columns", "utf8", "missing", "rank")
    for case in range(count):
        path = root / f"hdf-{case}.h5"
        np_arrays_to_hdf5({"value": np.arange(rng.randrange(1, 16), dtype=np.int64)}, str(path), "data")
        mutation = rng.choice(mutations)
        with h5py.File(path, "a") as file:
            group = file["data"]
            if mutation == "format":
                group.attrs["format"] = "unknown"
            elif mutation == "version":
                group.attrs["schema_version"] = rng.choice((-1, 0, 2, 2**31))
            elif mutation == "state":
                group.attrs["state"] = rng.choice(("", "writing", "aborted"))
            elif mutation == "rows":
                group.attrs["rows"] = rng.choice((-1, 0, 2**63 - 1))
            elif mutation == "columns":
                group.attrs["columns_json"] = rng.choice(("", "null", "{}", '["value","value"]'))
            elif mutation == "utf8":
                group.attrs["utf8_columns_json"] = '["missing"]'
            elif mutation == "missing":
                del group["value"]
            else:
                del group["value"]
                group.create_dataset("value", data=np.ones((2, 2)))
        try:
            hdf5_to_np_arrays(str(path), "data", max_columns=32, max_rows=1_024, max_bytes=1 << 20)
        except (KeyError, RuntimeError, TypeError, ValueError, OverflowError):
            pass


def main() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="gambit-fuzz-") as directory:
        root = Path(directory)
        for seed in config["seeds"]:
            rng = random.Random(seed)
            count = config["cases_per_seed"]
            _fuzz_csv(root, rng, count, config["max_payload_bytes"])
            _fuzz_zip(root, rng, count)
            _fuzz_hdf5(root, rng, count)


if __name__ == "__main__":
    main()
