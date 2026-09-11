import random
import struct
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import polars as pl
import pytest

from gambit.bundle_limits import BacktestBundleError, BundleLoadLimits
from gambit.ipc_validation import _message, _Metadata, inspect_ipc


def schema(frame):
    return [{"name": name, "dtype": str(dtype)} for name, dtype in frame.schema.items()]


def inspect(frame, data=None, limits=None):
    return inspect_ipc(frame.write_ipc(None).getvalue() if data is None else data,
                       schema(frame), frame.height, limits or BundleLoadLimits())


@pytest.mark.parametrize("dtype,values", [
    (pl.Int8, [-1, None]), (pl.Int16, [-1, None]), (pl.Int32, [-1, None]), (pl.Int64, [-1, None]),
    (pl.UInt8, [1, None]), (pl.UInt16, [1, None]), (pl.UInt32, [1, None]), (pl.UInt64, [1, None]),
    (pl.Float32, [1., None]), (pl.Float64, [1., None]), (pl.Boolean, [True, None]),
    (pl.String, ["héllo world long string!", None]), (pl.Binary, [b"\xffbinary payload data", None]),
    (pl.Null, [None, None]), (pl.Date, [date(2026, 1, 1), None]), (pl.Time, [time(12, 1), None]),
    (pl.Datetime("ns"), [datetime(2026, 1, 1), None]),
    (pl.Datetime("us", "UTC"), [datetime(2026, 1, 1), None]),
    (pl.Datetime("ms"), [datetime(2026, 1, 1), None]),
    (pl.Duration("ns"), [timedelta(seconds=1), None]),
    (pl.Decimal(10, 2), [Decimal("1.25"), None]),
])
@pytest.mark.parametrize("latest", [False, True])
def test_supported_primitive_round_trips(dtype, values, latest):
    frame = pl.DataFrame({"value": values}, schema={"value": dtype})
    data = frame.write_ipc(None, compat_level=pl.CompatLevel.newest() if latest else pl.CompatLevel.oldest()).getvalue()
    assert inspect(frame, data) > 0
    assert pl.read_ipc(data).equals(frame)
    assert inspect(frame.clear()) >= 0


def batch_locations(data):
    footer_size = struct.unpack_from("<i", data, len(data) - 10)[0]
    footer = _Metadata(memoryview(data)[-10 - footer_size:-10]).root()
    blocks, count = footer.vector(3, 24, 1024)
    offset = footer.metadata.number(blocks, "q")
    message, meta_size, _ = _message(memoryview(data), offset, len(data) - 10 - footer_size, BundleLoadLimits())
    batch = message.child(2)
    prefix = 8 if data[offset:offset + 4] == b"\xff" * 4 else 4
    return footer, blocks, count, batch, offset + prefix, offset + meta_size


@pytest.mark.parametrize("target", ["rows", "node_length", "null_count", "buffer_length", "buffer_offset"])
def test_forged_huge_or_invalid_dimensions_in_tiny_file(target):
    frame = pl.DataFrame({"x": [1, 2]})
    data = bytearray(frame.write_ipc(None).getvalue())
    _, _, _, batch, base, _ = batch_locations(data)
    if target == "rows":
        position = batch.field(0, 8)
    elif target in ("node_length", "null_count"):
        position, _ = batch.vector(1, 16, 128)
        if target == "null_count":
            position += 8
    else:
        position, _ = batch.vector(2, 16, 128)
        position += 16 + (8 if target == "buffer_length" else 0)
    assert position is not None and len(data) < 1024
    struct.pack_into("<q", data, base + position, 2**50)
    with pytest.raises(BacktestBundleError):
        inspect(frame, bytes(data))


def test_record_batch_count_and_cumulative_row_limits():
    frame = pl.DataFrame({"x": range(10)})
    data = frame.write_ipc(None, record_batch_size=2).getvalue()
    assert inspect(frame, data) > 0
    with pytest.raises(BacktestBundleError, match="vector count limit"):
        inspect(frame, data, BundleLoadLimits(max_record_batches=1))
    with pytest.raises(BacktestBundleError, match="table row limit"):
        inspect(frame, data, BundleLoadLimits(max_table_rows=5))


def test_encapsulated_schema_message_is_supported():
    frame = pl.DataFrame({"x": [1, 2]})
    original = frame.write_ipc(None).getvalue()
    footer, blocks, count, _, _, _ = batch_locations(original)
    schema_size = footer.metadata.number(blocks, "q") - 8
    data = bytearray(original[:8] + struct.pack("<Ii", 0xFFFFFFFF, schema_size) + original[8:])
    footer_size = struct.unpack_from("<i", data, len(data) - 10)[0]
    footer_start = len(data) - 10 - footer_size
    for i in range(count):
        struct.pack_into("<q", data, footer_start + blocks + 24 * i,
                         footer.metadata.number(blocks + 24 * i, "q") + 8)
    assert inspect(frame, bytes(data)) > 0
    assert pl.read_ipc(bytes(data)).equals(frame)


def test_repeated_view_payload_is_charged_for_logical_expansion():
    frame = pl.DataFrame({"x": ["long view string data" * 50] * 10})
    data = frame.write_ipc(None, compat_level=pl.CompatLevel.newest()).getvalue()
    cost = inspect(frame, data)
    with pytest.raises(BacktestBundleError, match="decoded byte limit"):
        inspect(frame, data, BundleLoadLimits(max_table_decoded_bytes=cost - 1))
    assert inspect(frame, data, BundleLoadLimits(max_table_decoded_bytes=cost)) == cost


@pytest.mark.parametrize("latest", [False, True])
def test_corrupt_variable_value_references_fail_before_native_decode(latest):
    frame = pl.DataFrame({"x": ["long view string data" * 2]})
    data = bytearray(frame.write_ipc(None, compat_level=pl.CompatLevel.newest() if latest
                                    else pl.CompatLevel.oldest()).getvalue())
    _, _, _, batch, _, body_start = batch_locations(data)
    buffers, _ = batch.vector(2, 16, 128)
    value_start = batch.metadata.number(buffers + 16, "q")
    # Oversized string-view length or negative variable-width first offset.
    struct.pack_into("<i" if latest else "<q", data, body_start + value_start, 2**30 if latest else -1)
    with pytest.raises(BacktestBundleError):
        inspect(frame, bytes(data))


def test_seeded_metadata_mutations_and_truncations_raise_only_bundle_errors():
    frame = pl.DataFrame({"i": [1, None], "s": ["hello", "long text with buffer"]})
    original = frame.write_ipc(None).getvalue()
    randomizer = random.Random(90210)
    inputs = [original[:i] for i in range(0, len(original), 13)]
    for _ in range(200):
        data = bytearray(original)
        index = randomizer.randrange(len(data))
        data[index] ^= randomizer.randrange(1, 256)
        inputs.append(bytes(data))
    for data in inputs:
        try:
            inspect(frame, data)
        except BacktestBundleError:
            pass
        # This test exercises the preflight only; admitted payload contents are
        # still checked by Polars, and this is not a native decoder fuzz claim.
