import json

import h5py
import numpy as np
import pytest

from gambit.pq_io import HDF5_FORMAT, HDF5_SCHEMA_VERSION, hdf5_to_np_arrays, np_arrays_to_hdf5


def test_versioned_hdf5_round_trip_has_committed_manifest(tmp_path) -> None:
    filename = tmp_path / "market.h5"
    source = {"symbol": np.array(["ES", "NQ"]), "price": np.array([5000.0, 18000.0])}

    np_arrays_to_hdf5(source, str(filename), "ticks/equity-index")

    result = hdf5_to_np_arrays(str(filename), "ticks/equity-index")
    assert result["symbol"].tolist() == ["ES", "NQ"]
    assert result["price"].tolist() == [5000.0, 18000.0]
    with h5py.File(filename, "r") as file:
        group = file["ticks/equity-index"]
        assert group.attrs["format"] == HDF5_FORMAT
        assert group.attrs["schema_version"] == HDF5_SCHEMA_VERSION
        assert group.attrs["state"] == "committed"
        assert json.loads(group.attrs["columns_json"]) == ["symbol", "price"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"a": np.array([1]), "b": np.array([1, 2])}, "same row count"),
        ({"a/b": np.array([1])}, "column name"),
        ({"a": np.ones((1, 1))}, "one-dimensional"),
    ],
)
def test_invalid_hdf5_input_fails_before_file_mutation(tmp_path, data, message) -> None:
    filename = tmp_path / "invalid.h5"

    with pytest.raises(ValueError, match=message):
        np_arrays_to_hdf5(data, str(filename), "data")

    assert not filename.exists()


def test_failed_replacement_preserves_previous_committed_group(tmp_path) -> None:
    filename = tmp_path / "replace.h5"
    np_arrays_to_hdf5({"value": np.array([1, 2])}, str(filename), "data")

    with pytest.raises(ValueError):
        np_arrays_to_hdf5(
            {"value": np.array([3, 4])},
            str(filename),
            "data",
            compression_args={"compression": "not-a-real-filter"},
        )

    assert hdf5_to_np_arrays(str(filename), "data")["value"].tolist() == [1, 2]
    with h5py.File(filename, "r") as file:
        assert "data.__gambit_pending" not in file


def test_reader_recovers_committed_backup_after_interrupted_swap(tmp_path) -> None:
    filename = tmp_path / "recover.h5"
    np_arrays_to_hdf5({"value": np.array([1, 2])}, str(filename), "data")
    with h5py.File(filename, "a") as file:
        file.move("data", "data.__gambit_backup")
        pending = file.create_group("data.__gambit_pending")
        pending.attrs["state"] = "writing"

    assert hdf5_to_np_arrays(str(filename), "data")["value"].tolist() == [1, 2]

    np_arrays_to_hdf5({"value": np.array([3, 4])}, str(filename), "data")
    assert hdf5_to_np_arrays(str(filename), "data")["value"].tolist() == [3, 4]
    with h5py.File(filename, "r") as file:
        assert list(file) == ["data"]


def test_reader_rejects_unknown_schema_and_inconsistent_rows(tmp_path) -> None:
    filename = tmp_path / "corrupt.h5"
    np_arrays_to_hdf5({"value": np.array([1, 2])}, str(filename), "data")
    with h5py.File(filename, "a") as file:
        file["data"].attrs["schema_version"] = 999

    with pytest.raises(ValueError, match="unsupported.*schema"):
        hdf5_to_np_arrays(str(filename), "data")

    with h5py.File(filename, "a") as file:
        file["data"].attrs["schema_version"] = HDF5_SCHEMA_VERSION
        file["data"].attrs["rows"] = 3

    with pytest.raises(ValueError, match="inconsistent rows"):
        hdf5_to_np_arrays(str(filename), "data")


def test_hdf5_resource_limits_apply_before_read_or_write_allocation(tmp_path) -> None:
    filename = tmp_path / "limits.h5"
    source = {"value": np.arange(4, dtype=np.int64)}

    with pytest.raises(ValueError, match="input size exceeds"):
        np_arrays_to_hdf5(source, str(filename), "data", max_bytes=8)

    np_arrays_to_hdf5(source, str(filename), "data")
    with pytest.raises(ValueError, match="dataset size exceeds"):
        hdf5_to_np_arrays(str(filename), "data", max_bytes=8)


def test_object_payload_counts_toward_write_limit(tmp_path) -> None:
    filename = tmp_path / "objects.h5"
    source = {"value": np.array(["x" * 1_024], dtype=object)}

    with pytest.raises(ValueError, match="input size exceeds"):
        np_arrays_to_hdf5(source, str(filename), "data", max_bytes=128)

    assert not filename.exists()


def test_reader_rejects_unbounded_variable_length_dataset(tmp_path) -> None:
    filename = tmp_path / "variable.h5"
    with h5py.File(filename, "w") as file:
        group = file.create_group("data")
        group.attrs.update(
            {
                "type": "dataframe",
                "format": HDF5_FORMAT,
                "schema_version": HDF5_SCHEMA_VERSION,
                "state": "committed",
                "rows": 1,
                "columns_json": '["value"]',
                "utf8_columns_json": "[]",
            }
        )
        group.create_dataset("value", data=np.array(["payload"], dtype=object), dtype=h5py.string_dtype())

    with pytest.raises(ValueError, match="variable-length"):
        hdf5_to_np_arrays(str(filename), "data")


def test_reader_remains_compatible_with_legacy_dataframe_manifest(tmp_path) -> None:
    filename = tmp_path / "legacy.h5"
    with h5py.File(filename, "w") as file:
        group = file.create_group("data")
        group.attrs.update({"type": "dataframe", "rows": 2, "columns": "a,b", "utf8_cols": ""})
        group.create_dataset("a", data=np.array([1, 2]))
        group.create_dataset("b", data=np.array([3.0, 4.0]))

    result = hdf5_to_np_arrays(str(filename), "data")

    assert result["a"].tolist() == [1, 2]
    assert result["b"].tolist() == [3.0, 4.0]
