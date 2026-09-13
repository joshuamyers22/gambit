"""Bounded Python IPC-preflight fuzz target; never decode mutated native payloads."""

import polars as pl

from gambit.bundle_limits import BacktestBundleError, BundleLoadLimits
from gambit.ipc_validation import inspect_ipc

MAX_INPUT_BYTES = 65536
LIMITS = BundleLoadLimits(max_table_bytes=MAX_INPUT_BYTES, max_table_rows=64,
                          max_columns=8, max_record_batches=16,
                          max_ipc_metadata_bytes=32768, max_table_decoded_bytes=262144)
# Only these trusted synthetic frames are serialized by Polars. The two-byte
# fuzz header selects the expected schema and a bounded manifest row count.
FRAMES = (
    pl.DataFrame({"value": [-(2**63), None, 2**63 - 1]}, schema={"value": pl.Int64}),
    pl.DataFrame({"value": ["héllo", None, "a longer out-of-line string"]}),
    pl.DataFrame({"value": [b"\xff", None, b"a longer binary payload"]}),
    pl.DataFrame({"flag": [True, None, False], "value": [1.5, None, -2.5], "empty": [None] * 3}),
)
SCHEMAS = [[{"name": name, "dtype": str(dtype)} for name, dtype in frame.schema.items()] for frame in FRAMES]


def seed_inputs() -> list[bytes]:
    seeds = [b"", b"\x00\x00ARROW1", b"\xff" * 18]
    for index, frame in enumerate(FRAMES):
        for version in (pl.CompatLevel.oldest(), pl.CompatLevel.newest()):
            for source in (frame, frame.clear()):
                payload = source.write_ipc(None, compat_level=version, record_batch_size=2).getvalue()
                seeds.append(bytes((index, source.height)) + payload)
    return seeds


def test_one_input(data: bytes) -> bool:
    """Reject documented invalid inputs; let every other exception fail fuzzing."""
    if len(data) < 2 or len(data) > MAX_INPUT_BYTES:
        return False
    expected_schema = SCHEMAS[data[0] % len(SCHEMAS)]
    expected_rows = data[1] % (LIMITS.max_table_rows + 1)
    try:
        decoded = inspect_ipc(data[2:], expected_schema, expected_rows, LIMITS)
    except BacktestBundleError:
        return False
    if not 0 <= decoded <= LIMITS.max_table_decoded_bytes:
        raise AssertionError("IPC preflight admitted an out-of-budget decoded payload")
    return True
