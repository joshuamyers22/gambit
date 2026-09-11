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


@pytest.mark.parametrize("mutation", ["missing", "rows", "rank", "variable", "utf8_numeric", "budget"])
def test_all_columns_are_preflighted_before_any_dataset_read(tmp_path, monkeypatch, mutation):
    filename = tmp_path / "preflight.h5"
    np_arrays_to_hdf5({"first": np.arange(2), "second": np.arange(2)}, str(filename), "data")
    with h5py.File(filename, "a") as file:
        group = file["data"]
        if mutation in {"missing", "rows", "rank", "variable"}:
            del group["second"]
        if mutation == "rows":
            group.create_dataset("second", data=np.arange(3))
        elif mutation == "rank":
            group.create_dataset("second", data=np.ones((2, 2)))
        elif mutation == "variable":
            group.create_dataset("second", data=["a", "b"], dtype=h5py.string_dtype())
        elif mutation == "utf8_numeric":
            group.attrs["utf8_columns_json"] = '["second"]'
    reads = []
    original = h5py.Dataset.__getitem__

    def observe(dataset, selection, *args, **kwargs):
        reads.append(dataset.name)
        return original(dataset, selection, *args, **kwargs)

    monkeypatch.setattr(h5py.Dataset, "__getitem__", observe)
    with pytest.raises(ValueError):
        hdf5_to_np_arrays(str(filename), "data", max_bytes=16 if mutation == "budget" else 1024)
    assert reads == [], "rejected metadata must not materialize even an earlier valid column"


@pytest.mark.parametrize("attribute,value", [
    ("rows", True), ("rows", 2.5), ("rows", "2"), ("rows", np.array([2])),
    ("schema_version", True), ("schema_version", 1.5), ("schema_version", "1"),
])
def test_hdf5_integer_attributes_do_not_coerce_invalid_types(tmp_path, attribute, value):
    filename = tmp_path / "types.h5"
    np_arrays_to_hdf5({"value": np.arange(2)}, str(filename), "data")
    with h5py.File(filename, "a") as file:
        file["data"].attrs[attribute] = value
    with pytest.raises(ValueError, match="integer"):
        hdf5_to_np_arrays(str(filename), "data")


@pytest.mark.parametrize("columns", ['[[]]', '[{}]', '[1]', '["value","value"]', '["nested/value"]', '["."]', '["value\\u0000alias"]'])
def test_hdf5_malformed_column_names_fail_with_value_error(tmp_path, columns):
    filename = tmp_path / "names.h5"
    np_arrays_to_hdf5({"value": np.arange(2)}, str(filename), "data")
    with h5py.File(filename, "a") as file:
        file["data"].attrs["columns_json"] = columns
    with pytest.raises(ValueError, match="manifest"):
        hdf5_to_np_arrays(str(filename), "data")


@pytest.mark.parametrize("location", ["group", "parent", "column", "backup"])
@pytest.mark.parametrize("kind", ["soft", "external"])
def test_reader_rejects_link_indirection_before_resolving_it(tmp_path, monkeypatch, location, kind):
    filename = tmp_path / "links.h5"
    target = tmp_path / "target.h5"
    for path in (filename, target):
        np_arrays_to_hdf5({"value": np.arange(2)}, str(path), "actual/data")
    key = "data"
    destination = "actual/data"
    if location == "parent":
        key, destination = "alias/data", "actual"
        link_name = "alias"
    elif location == "column":
        key, destination = "actual/data", "actual/data/value"
        link_name = "actual/data/value"
    else:
        link_name = "data.__gambit_backup" if location == "backup" else "data"
    with h5py.File(filename, "a") as file:
        if location == "column":
            # Keep a same-file target for the soft-link case.
            file["saved"] = file[destination]
            del file[link_name]
            if kind == "soft":
                destination = "saved"
        file[link_name] = (h5py.SoftLink("/" + destination) if kind == "soft"
                           else h5py.ExternalLink(str(target), "/" + destination))
    original = h5py.Group.__getitem__
    resolved_links = []

    def observe(group, name):
        link = group.get(name, getlink=True)
        if isinstance(link, (h5py.SoftLink, h5py.ExternalLink)):
            resolved_links.append(name)
        return original(group, name)

    monkeypatch.setattr(h5py.Group, "__getitem__", observe)
    with pytest.raises(ValueError, match="link"):
        hdf5_to_np_arrays(str(filename), key)
    assert resolved_links == []


@pytest.mark.parametrize("layout", ["external", "virtual"])
def test_reader_rejects_external_dataset_storage_before_read(tmp_path, monkeypatch, layout):
    filename = tmp_path / "external-layout.h5"
    np_arrays_to_hdf5({"value": np.arange(2)}, str(filename), "data")
    with h5py.File(filename, "a") as file:
        group = file["data"]
        del group["value"]
        if layout == "external":
            group.create_dataset("value", shape=(2,), dtype="i8",
                                 external=[(str(tmp_path / "not-to-be-read.bin"), 0, 16)])
        else:
            virtual = h5py.VirtualLayout(shape=(2,), dtype="i8")
            virtual[:] = h5py.VirtualSource(str(tmp_path / "not-to-be-read.h5"), "value", shape=(2,))
            group.create_virtual_dataset("value", virtual)

    def reject_read(*args, **kwargs):
        raise AssertionError("external payload read attempted")

    monkeypatch.setattr(h5py.Dataset, "__getitem__", reject_read)
    with pytest.raises(ValueError, match="external|virtual"):
        hdf5_to_np_arrays(str(filename), "data")


@pytest.mark.parametrize("as_utf8", [[], ["value"]])
def test_hdf5_budget_counts_decoded_unicode_width(tmp_path, as_utf8):
    filename = tmp_path / "unicode.h5"
    np_arrays_to_hdf5({"value": np.array([b"abcd", b"efgh"])}, str(filename), "data", as_utf8=as_utf8)
    with pytest.raises(ValueError, match="dataset size exceeds"):
        hdf5_to_np_arrays(str(filename), "data", max_bytes=31)
    result = hdf5_to_np_arrays(str(filename), "data", max_bytes=32)
    assert result["value"].tolist() == ["abcd", "efgh"]
    assert result["value"].nbytes == 32


def test_hard_link_aliases_are_supported_and_each_output_counts(tmp_path):
    filename = tmp_path / "hard-links.h5"
    np_arrays_to_hdf5({"first": np.arange(2, dtype="i8")}, str(filename), "data")
    with h5py.File(filename, "a") as file:
        group = file["data"]
        group["second"] = group["first"]
        group.attrs["columns_json"] = '["first","second"]'
    with pytest.raises(ValueError, match="dataset size exceeds"):
        hdf5_to_np_arrays(str(filename), "data", max_bytes=31)
    result = hdf5_to_np_arrays(str(filename), "data", max_bytes=32)
    assert result["first"].tolist() == result["second"].tolist() == [0, 1]
    assert not np.shares_memory(result["first"], result["second"])


@pytest.mark.parametrize("key", ["data\x00alias", "parent\x00alias/data"])
def test_hdf5_keys_reject_nul_before_opening_file(tmp_path, key):
    filename = tmp_path / "not-created.h5"
    with pytest.raises(ValueError, match="key"):
        np_arrays_to_hdf5({"value": np.arange(2)}, str(filename), key)
    with pytest.raises(ValueError, match="key"):
        hdf5_to_np_arrays(str(filename), key)
    assert not filename.exists()


def test_hdf5_writer_rejects_nul_column_before_mutating_file(tmp_path):
    filename = tmp_path / "not-created.h5"
    with pytest.raises(ValueError, match="column name"):
        np_arrays_to_hdf5({"value\x00alias": np.arange(2)}, str(filename), "data")
    assert not filename.exists()


def test_hdf5_fixed_width_unicode_round_trip_with_non_ascii_and_empty_values(tmp_path):
    filename = tmp_path / "strings.h5"
    values = np.array(["é", "你好", ""])
    np_arrays_to_hdf5({"value": values}, str(filename), "data")
    assert hdf5_to_np_arrays(str(filename), "data")["value"].tolist() == values.tolist()


def test_hdf5_rejects_variable_length_inside_compound_dtype(tmp_path, monkeypatch):
    filename = tmp_path / "compound.h5"
    np_arrays_to_hdf5({"value": np.arange(2)}, str(filename), "data")
    with h5py.File(filename, "a") as file:
        del file["data/value"]
        file["data"].create_dataset("value", shape=(2,), dtype=np.dtype([("text", h5py.string_dtype())]))

    def reject_read(*args, **kwargs):
        raise AssertionError("variable-length compound payload was read")

    monkeypatch.setattr(h5py.Dataset, "__getitem__", reject_read)
    with pytest.raises(ValueError, match="variable-length"):
        hdf5_to_np_arrays(str(filename), "data")
