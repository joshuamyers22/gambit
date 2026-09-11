import hashlib
import json
import os
from dataclasses import replace
from types import SimpleNamespace

import polars as pl
import pytest

from gambit.backtest_result import BacktestBundleError, BacktestResult
from gambit.bundle_limits import BundleLoadLimits
from test_backtest_result import _strategy


@pytest.fixture
def bundle(tmp_path):
    return _strategy().run().save(tmp_path / "bundle")


def edit_manifest(bundle, change):
    path = bundle / "manifest.json"
    value = json.loads(path.read_bytes())
    change(value)
    path.write_text(json.dumps(value))


def replace_frame(bundle, frame, name="trades", compression="uncompressed"):
    path = bundle / f"{name}.arrow"
    frame.write_ipc(path, compression=compression)
    edit_manifest(bundle, lambda m: m["frames"].__setitem__(name, {
        "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows": frame.height, "schema": [{"name": n, "dtype": str(t)} for n, t in frame.schema.items()],
    }))


@pytest.fixture
def no_decode(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Arrow data was materialized before all admission checks passed")
    monkeypatch.setattr(pl, "read_ipc", fail)


@pytest.mark.parametrize("raw", [b"[]", b"null", b"1", b'"text"', b'{}', b'\xff',
                                   b'{"version":4,"version":4}', b'{"x":NaN}',
                                   pytest.param(b'[' * 2000, id="excessive-depth")])
def test_malformed_manifest_has_consistent_error(bundle, no_decode, raw):
    (bundle / "manifest.json").write_bytes(raw)
    with pytest.raises(BacktestBundleError):
        BacktestResult.load(bundle)


@pytest.mark.parametrize("section,value", [("frames", []), ("frames", None), ("provenance", []),
                                            ("telemetry", []), ("version", 4.0), ("version", True)])
def test_bad_top_level_metadata_fails_before_decode(bundle, no_decode, section, value):
    edit_manifest(bundle, lambda m: m.__setitem__(section, value))
    with pytest.raises(BacktestBundleError):
        BacktestResult.load(bundle)


@pytest.mark.parametrize("field,value", [("rows", True), ("rows", -1), ("rows", "1"), ("schema", {}),
    ("schema", [None]), ("schema", [{"name": [], "dtype": "Int64"}]), ("sha256", "bad"), ("file", "../escape")])
def test_invalid_frame_metadata_fails_before_decode(bundle, no_decode, field, value):
    edit_manifest(bundle, lambda m: m["frames"]["validation_findings"].__setitem__(field, value))
    with pytest.raises(BacktestBundleError):
        BacktestResult.load(bundle)


@pytest.mark.parametrize("limit", ["max_manifest_bytes", "max_table_bytes", "max_total_bytes", "max_columns",
                                    "max_ipc_metadata_bytes", "max_table_decoded_bytes", "max_total_decoded_bytes"])
def test_small_limits_fail_before_decode(bundle, no_decode, limit):
    with pytest.raises(BacktestBundleError, match="limit|budget"):
        BacktestResult.load(bundle, limits=replace(BundleLoadLimits(), **{limit: 1}))


@pytest.mark.parametrize("value", [0, -1, True, 1.5, None])
def test_limits_require_positive_integers(value):
    with pytest.raises(ValueError, match="positive integer"):
        BundleLoadLimits(max_manifest_bytes=value)


def test_manifest_exact_byte_limit_and_one_byte_over(bundle):
    size = (bundle / "manifest.json").stat().st_size
    assert BacktestResult.load(bundle, limits=BundleLoadLimits(max_manifest_bytes=size)).trades.height == 1
    with pytest.raises(BacktestBundleError, match="limit"):
        BacktestResult.load(bundle, limits=BundleLoadLimits(max_manifest_bytes=size - 1))


@pytest.mark.parametrize("compression", ["lz4", "zstd"])
def test_checksum_valid_compressed_frame_rejected_before_decode(bundle, no_decode, compression):
    replace_frame(bundle, pl.DataFrame({"s": ["repeat" * 1000] * 10}), compression=compression)
    with pytest.raises(BacktestBundleError, match="compressed"):
        BacktestResult.load(bundle)


@pytest.mark.parametrize("frame", [pl.DataFrame({"nested": [[1, 2]]}),
    pl.DataFrame({"struct": [{"x": 1}]}), pl.DataFrame({"c": ["a"]}, schema={"c": pl.Categorical})])
def test_unsupported_layout_rejected_before_decode(bundle, no_decode, frame):
    replace_frame(bundle, frame)
    with pytest.raises(BacktestBundleError, match="unsupported|dictionary|vector"):
        BacktestResult.load(bundle)


def test_actual_ipc_rows_not_manifest_claim_control_admission(bundle, no_decode):
    replace_frame(bundle, pl.DataFrame({"n": [None] * 10}))
    edit_manifest(bundle, lambda m: m["frames"]["trades"].__setitem__("rows", 1))
    with pytest.raises(BacktestBundleError, match="row"):
        BacktestResult.load(bundle, limits=BundleLoadLimits(max_table_rows=5))


def test_aggregate_rows_checked_before_decode(bundle, no_decode):
    with pytest.raises(BacktestBundleError, match="row"):
        BacktestResult.load(bundle, limits=BundleLoadLimits(max_total_rows=1))


@pytest.mark.parametrize("extra", [False, True])
def test_exact_frame_set_enforced_before_decode(bundle, no_decode, extra):
    def change(manifest):
        if extra:
            manifest["frames"]["unexpected"] = dict(manifest["frames"]["trades"])
        else:
            del manifest["frames"]["trades"]
    edit_manifest(bundle, change)
    with pytest.raises(BacktestBundleError, match="unexpected frame set"):
        BacktestResult.load(bundle)


def test_bad_last_frame_prevents_materialization_of_earlier_frames(bundle, no_decode):
    path = bundle / "validation_findings.arrow"
    path.write_bytes(b"not Arrow")
    edit_manifest(bundle, lambda m: m["frames"]["validation_findings"].__setitem__(
        "sha256", hashlib.sha256(path.read_bytes()).hexdigest()))
    with pytest.raises(BacktestBundleError, match="IPC"):
        BacktestResult.load(bundle)


@pytest.mark.parametrize("name", ["manifest.json", "trades.arrow"])
def test_symlink_member_rejected(bundle, no_decode, name):
    path = bundle / name
    moved = bundle.parent / name
    path.rename(moved)
    path.symlink_to(moved)
    with pytest.raises(BacktestBundleError):
        BacktestResult.load(bundle)


def test_decode_uses_validated_bytes_not_reopened_paths(bundle, monkeypatch):
    read = pl.read_ipc
    calls = []
    def decode(source, **kwargs):
        assert isinstance(source, bytes)
        calls.append(source)
        # Path replacement after preflight must not change the bytes decoded.
        (bundle / "decisions.arrow").write_bytes(b"replaced")
        return read(source, **kwargs)
    monkeypatch.setattr(pl, "read_ipc", decode)
    assert BacktestResult.load(bundle).decisions.height == 1
    assert len(calls) == 9


def test_size_check_cannot_be_bypassed_by_growth_after_fstat(bundle, no_decode, monkeypatch):
    original = os.fstat
    def stat_before_growth(descriptor):
        info = original(descriptor)
        return SimpleNamespace(st_mode=info.st_mode, st_size=0)
    monkeypatch.setattr(os, "fstat", stat_before_growth)
    with pytest.raises(BacktestBundleError, match="byte limit"):
        BacktestResult.load(bundle, limits=BundleLoadLimits(max_manifest_bytes=1))


def test_fifo_member_does_not_block(bundle, no_decode):
    path = bundle / "trades.arrow"
    path.rename(bundle / "original.arrow")
    os.mkfifo(path)
    with pytest.raises(BacktestBundleError, match="regular file"):
        BacktestResult.load(bundle)


@pytest.mark.parametrize("section,key,value", [
    ("provenance", "configuration", None), ("provenance", "input_fingerprints", []),
    ("provenance", "package_version", []), ("provenance", "git_commit", 1),
    ("telemetry", "stages", {}), ("telemetry", "stages", [None]),
    ("telemetry", "orders_proposed", True), ("telemetry", "orders_proposed", -1),
])
def test_nested_metadata_types_checked_before_materialization(bundle, no_decode, section, key, value):
    edit_manifest(bundle, lambda m: m[section].__setitem__(key, value))
    with pytest.raises(BacktestBundleError):
        BacktestResult.load(bundle)
