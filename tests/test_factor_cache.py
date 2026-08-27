import gc
import struct

import numpy as np
import pytest

from gambit.factor_cache import (
    HEADER_BYTES,
    MappedFloat64Column,
    factor_node_key,
    read_reference_float64,
    write_reference_float64,
)


def test_factor_node_key_is_deterministic_and_parent_sensitive() -> None:
    arguments = {"parents": ("a",), "transform": "zscore:v1", "parameters": "window=20", "input_fingerprint": "raw"}
    assert factor_node_key(**arguments) == factor_node_key(**arguments)
    assert factor_node_key(**arguments) != factor_node_key(**{**arguments, "parents": ("b",)})


def test_reference_segment_round_trips(tmp_path) -> None:
    values = np.array([1.0, np.nan, -3.5], dtype=np.float64)
    path = tmp_path / "factor.bin"

    write_reference_float64(path, values)

    assert np.array_equal(read_reference_float64(path), values, equal_nan=True)


@pytest.mark.native
def test_native_segment_matches_reference_and_is_read_only(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    values = np.array([1.0, np.nan, -3.5, 9.25], dtype=np.float64)
    path = tmp_path / "native.bin"

    created = MappedFloat64Column.create(str(path), values)
    mapped = created.values

    assert np.array_equal(mapped, values, equal_nan=True)
    assert np.array_equal(read_reference_float64(path), values, equal_nan=True)
    assert not mapped.flags.writeable
    with pytest.raises(ValueError):
        mapped[0] = 2.0
    del created
    gc.collect()
    assert mapped[-1] == 9.25


@pytest.mark.native
def test_native_reader_rejects_corrupt_column(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    path = tmp_path / "corrupt.bin"
    write_reference_float64(path, np.array([1.0, 2.0]))
    with path.open("r+b") as file:
        file.seek(HEADER_BYTES)
        file.write(b"\xff")

    with pytest.raises(RuntimeError, match="checksum"):
        MappedFloat64Column.open(str(path))


@pytest.mark.native
def test_native_reader_rejects_uncommitted_and_truncated_segments(tmp_path) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    uncommitted = tmp_path / "uncommitted.bin"
    write_reference_float64(uncommitted, np.array([1.0]))
    with uncommitted.open("r+b") as file:
        file.seek(12)
        file.write(struct.pack("<I", 0))
    with pytest.raises(RuntimeError, match="not committed"):
        MappedFloat64Column.open(str(uncommitted))

    truncated = tmp_path / "truncated.bin"
    truncated.write_bytes(b"GAMBITFC")
    with pytest.raises(RuntimeError, match="truncated"):
        MappedFloat64Column.open(str(truncated))
