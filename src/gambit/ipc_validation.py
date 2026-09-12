"""Bounded preflight for the flat, uncompressed Arrow IPC result profile.

Format references: https://arrow.apache.org/docs/format/Columnar.html and
https://github.com/apache/arrow/tree/main/format (File, Message, Schema.fbs).
This is deliberately not a general Arrow reader. All offsets/counts are checked
in Python before Polars sees the immutable bytes; unsupported layouts fail shut.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import polars as pl

from gambit.bundle_limits import BacktestBundleError, BundleLoadLimits


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BacktestBundleError(f"invalid or unsupported IPC: {message}")


class _Metadata:
    """Small bounds-checked FlatBuffer accessor, with no recursive traversal."""

    def __init__(self, data: memoryview):
        self.data = data

    def span(self, offset: int, size: int) -> memoryview:
        _require(0 <= offset <= len(self.data) and 0 <= size <= len(self.data) - offset, "metadata bounds")
        return self.data[offset:offset + size]

    def number(self, offset: int, fmt: str) -> int:
        return int(struct.unpack("<" + fmt, self.span(offset, struct.calcsize("<" + fmt)))[0])

    def indirect(self, offset: int) -> int:
        target = offset + self.number(offset, "I")
        _require(target > offset, "invalid metadata reference")
        self.span(target, 4)
        return target

    def table(self, offset: int) -> _Table:
        return _Table(self, offset)

    def root(self) -> _Table:
        return self.table(self.indirect(0))


class _Table:
    def __init__(self, metadata: _Metadata, offset: int):
        self.metadata, self.offset = metadata, offset
        self.vtable = offset - metadata.number(offset, "i")
        self.vsize = metadata.number(self.vtable, "H")
        self.size = metadata.number(self.vtable + 2, "H")
        _require(4 <= self.vsize <= 36 and self.vsize % 2 == 0 and self.size >= 4, "table dimensions")
        metadata.span(self.vtable, self.vsize)
        metadata.span(offset, self.size)

    def field(self, index: int, width: int = 4) -> int | None:
        slot = 4 + 2 * index
        relative = self.metadata.number(self.vtable + slot, "H") if slot < self.vsize else 0
        if relative == 0:
            return None
        _require(4 <= relative and relative + width <= self.size, "field bounds")
        return self.offset + relative

    def number(self, index: int, fmt: str, default: int = 0) -> int:
        offset = self.field(index, struct.calcsize("<" + fmt))
        return default if offset is None else self.metadata.number(offset, fmt)

    def child(self, index: int) -> _Table:
        offset = self.field(index)
        if offset is None:
            raise BacktestBundleError("invalid IPC: missing required table")
        return self.metadata.table(self.metadata.indirect(offset))

    def vector(self, index: int, width: int, maximum: int) -> tuple[int, int]:
        offset = self.field(index)
        if offset is None:
            return 0, 0
        start = self.metadata.indirect(offset)
        count = self.metadata.number(start, "I")
        _require(count <= maximum, "vector count limit")
        self.metadata.span(start + 4, count * width)
        return start + 4, count

    def empty(self, index: int, width: int = 4) -> None:
        self.vector(index, width, 0)

    def string(self, index: int) -> str:
        start, size = self.vector(index, 1, 4096)
        if not start:
            return ""
        _require(self.metadata.number(start + size, "B") == 0, "unterminated string")
        try:
            return bytes(self.metadata.span(start, size)).decode("utf-8")
        except UnicodeDecodeError as error:
            raise BacktestBundleError("invalid IPC metadata UTF-8") from error


@dataclass(frozen=True)
class _Column:
    name: str
    dtype: str
    kind: int
    width: int  # fixed value width or variable offset width; bool uses 0


def _column(field: _Table) -> _Column:
    _require(field.field(4) is None, "dictionary encoding")
    field.empty(5)  # no nested children
    field.empty(6)  # no extension/custom metadata
    kind = field.number(2, "B")
    params = field.child(3)
    width = 0
    if kind == 1:
        dtype = str(pl.Null)
    elif kind == 2:
        bits = params.number(0, "i")
        signed = params.number(1, "B")
        _require(bits in (8, 16, 32, 64) and signed in (0, 1), "integer parameters")
        width, dtype = bits // 8, f"{'Int' if signed else 'UInt'}{bits}"
    elif kind == 3:
        precision = params.number(0, "h")
        _require(precision in (1, 2), "floating point precision")
        width = 4 if precision == 1 else 8
        dtype = f"Float{width * 8}"
    elif kind in (4, 5, 19, 20, 23, 24):
        width = 4 if kind in (4, 5) else 8
        dtype = "String" if kind in (5, 20, 24) else "Binary"
    elif kind == 6:
        dtype = "Boolean"
    elif kind == 7:
        precision, scale = params.number(0, "i"), params.number(1, "i")
        _require(params.number(2, "i", 128) == 128 and 1 <= precision <= 38 and 0 <= scale <= precision,
                 "decimal parameters")
        width, dtype = 16, str(pl.Decimal(precision, scale))
    elif kind == 8:
        _require(params.number(0, "h", 1) == 0, "only day-resolution dates supported")
        width, dtype = 4, "Date"
    elif kind == 9:
        _require(params.number(0, "h", 1) == 3 and params.number(1, "i", 32) == 64, "time parameters")
        width, dtype = 8, "Time"
    elif kind in (10, 18):
        unit = params.number(0, "h", 0 if kind == 10 else 1)
        _require(unit in (1, 2, 3), "temporal unit")
        unit_name = {1: "ms", 2: "us", 3: "ns"}[unit]
        dtype = str(pl.Datetime(unit_name, params.string(1) or None) if kind == 10 else pl.Duration(unit_name))
        width = 8
    else:
        raise BacktestBundleError(f"unsupported IPC type: {kind} (requires a flat primitive column)")
    return _Column(field.string(0), dtype, kind, width)


def _schema(table: _Table, limits: BundleLoadLimits) -> list[_Column]:
    _require(table.number(0, "h") == 0, "big-endian schema")
    table.empty(2)
    table.empty(3, 8)
    start, count = table.vector(1, 4, limits.max_columns)
    columns = [_column(table.metadata.table(table.metadata.indirect(start + 4 * i))) for i in range(count)]
    _require(len({column.name for column in columns}) == count, "duplicate column names")
    return columns


def _message(data: memoryview, offset: int, boundary: int, limits: BundleLoadLimits) -> tuple[_Table, int, int]:
    raw = _Metadata(data)
    prefix = 8 if raw.number(offset, "I") == 0xFFFFFFFF else 4
    size = raw.number(offset + prefix - 4, "i")
    _require(0 < size <= limits.max_ipc_metadata_bytes and offset + prefix + size <= boundary,
             "message metadata size limit")
    table = _Metadata(raw.span(offset + prefix, size)).root()
    _require(table.number(0, "h") in (3, 4), "metadata version")
    table.empty(4)
    body_size = table.number(3, "q")
    _require(0 <= body_size <= boundary - offset - prefix - size, "message body bounds")
    return table, prefix + size, body_size


def _batch(table: _Table, body: memoryview, columns: list[_Column], limits: BundleLoadLimits) -> tuple[int, int]:
    _require(table.field(3) is None, "compressed record batch")
    rows = table.number(0, "q")
    _require(0 <= rows <= limits.max_table_rows, "record batch row limit")
    nodes, node_count = table.vector(1, 16, limits.max_columns)
    _require(node_count == len(columns), "field node count")
    buffers, buffer_count = table.vector(2, 16, limits.max_ipc_metadata_bytes // 16)
    variadic, variadic_count = table.vector(4, 8, limits.max_columns)
    _require(variadic_count == sum(c.kind in (23, 24) for c in columns), "variadic buffer count")
    spans = []
    for i in range(buffer_count):
        offset = table.metadata.number(buffers + i * 16, "q")
        length = table.metadata.number(buffers + i * 16 + 8, "q")
        _require(0 <= offset <= len(body) and 0 <= length <= len(body) - offset, "buffer bounds")
        spans.append(body[offset:offset + length])
    # Include per-cell conversion/validity headroom and all referenced buffers,
    # not just unique body bytes: aliases must not evade the decoded budget.
    decoded = rows * len(columns) * 32 + 2 * sum(len(span) for span in spans)
    _require(decoded <= limits.max_table_decoded_bytes, "decoded byte limit")
    cursor = variadic_index = 0
    for index, column in enumerate(columns):
        length = table.metadata.number(nodes + index * 16, "q")
        nulls = table.metadata.number(nodes + index * 16 + 8, "q")
        _require(length == rows and 0 <= nulls <= rows, "field node length/null count")
        if column.kind == 1:
            continue
        extra = 0
        if column.kind in (23, 24):
            extra = table.metadata.number(variadic + variadic_index * 8, "q")
            variadic_index += 1
            _require(0 <= extra <= buffer_count, "variadic buffer count")
        count = 3 if column.kind in (4, 5, 19, 20) else 2 + extra
        _require(cursor + count <= len(spans), "missing column buffers")
        validity, values = spans[cursor:cursor + 2]
        _require(not nulls or len(validity) >= (rows + 7) // 8, "short validity bitmap")
        if column.kind in (4, 5, 19, 20):
            payload = spans[cursor + 2]
            _require(len(values) >= (rows + 1) * column.width, "short offsets buffer")
            previous = 0
            for (offset,) in struct.iter_unpack("<i" if column.width == 4 else "<q", values[:(rows + 1) * column.width]):
                _require(previous <= offset <= len(payload), "invalid string/binary offset")
                previous = offset
        elif column.kind in (23, 24):
            _require(len(values) >= rows * 16, "short view buffer")
            for i in range(rows):
                size, _, buffer_index, offset = struct.unpack_from("<iiii", values, i * 16)
                _require(size >= 0, "negative view size")
                if size > 12:
                    _require(0 <= buffer_index < extra, "view buffer index")
                    payload = spans[cursor + 2 + buffer_index]
                    _require(0 <= offset <= len(payload) and size <= len(payload) - offset, "view bounds")
                decoded += size  # also budget logical expansion of repeated views
                _require(decoded <= limits.max_table_decoded_bytes, "decoded byte limit")
        else:
            required = (rows + 7) // 8 if column.kind == 6 else rows * column.width
            _require(len(values) >= required, "short primitive buffer")
        cursor += count
    _require(cursor == len(spans), "unexpected buffers")
    return rows, decoded


def inspect_ipc(data: bytes, expected_schema: list[dict[str, str]], expected_rows: int,
                limits: BundleLoadLimits) -> int:
    """Validate metadata and body references; return bounded decoded payload cost."""
    _require(len(data) >= 18 and data[:6] == data[-6:] == b"ARROW1", "file magic")
    raw = _Metadata(memoryview(data))
    footer_size = raw.number(len(data) - 10, "i")
    _require(0 < footer_size <= limits.max_ipc_metadata_bytes and footer_size <= len(data) - 18,
             "footer metadata size limit")
    footer_start = len(data) - 10 - footer_size
    footer = _Metadata(raw.span(footer_start, footer_size)).root()
    _require(footer.number(0, "h") in (3, 4), "footer version")
    footer.empty(2, 24)  # dictionaries
    footer.empty(4)
    columns = _schema(footer.child(1), limits)
    _require([{"name": c.name, "dtype": c.dtype} for c in columns] == expected_schema, "schema mismatch")
    blocks, count = footer.vector(3, 24, limits.max_record_batches)
    schema_end = footer.metadata.number(blocks, "q") if count else footer_start
    if not count and data[schema_end - 8:schema_end] == b"\xff" * 4 + b"\x00" * 4:
        schema_end -= 8
    _require(8 < schema_end <= footer_start and schema_end - 8 <= limits.max_ipc_metadata_bytes,
             "schema metadata size limit")
    # Polars also writes an unframed schema Message directly after file magic.
    # Bound it by the first indexed batch (or footer), not by untrusted offsets
    # inside the schema itself. Encapsulated schema Messages remain supported.
    if raw.number(8, "I") == 0xFFFFFFFF:
        schema_message, schema_size, body_size = _message(raw.data, 8, schema_end, limits)
        _require(8 + schema_size == schema_end, "schema message size")
    else:
        schema_size = schema_end - 8
        schema_message = _Metadata(raw.span(8, schema_size)).root()
        _require(schema_message.number(0, "h") in (3, 4), "schema message version")
        schema_message.empty(4)
        body_size = schema_message.number(3, "q")
    _require(schema_message.number(1, "B") == 1 and body_size == 0, "missing schema message")
    _require(_schema(schema_message.child(2), limits) == columns, "inconsistent file schemas")
    metadata_bytes = footer_size + schema_size
    end = 8 + schema_size
    rows = decoded = 0
    for i in range(count):
        offset = footer.metadata.number(blocks + i * 24, "q")
        metadata_size = footer.metadata.number(blocks + i * 24 + 8, "i")
        body_length = footer.metadata.number(blocks + i * 24 + 16, "q")
        _require(offset == end, "overlapping, reordered, or unindexed blocks")
        message, actual_size, actual_body = _message(raw.data, offset, footer_start, limits)
        _require((metadata_size, body_length) == (actual_size, actual_body), "block lengths disagree")
        _require(message.number(1, "B") == 3, "non-record batch message")
        metadata_bytes += actual_size
        _require(metadata_bytes <= limits.max_ipc_metadata_bytes, "total metadata byte limit")
        batch_rows, batch_decoded = _batch(message.child(2), raw.span(offset + actual_size, actual_body), columns, limits)
        rows += batch_rows
        decoded += batch_decoded
        _require(rows <= limits.max_table_rows and rows <= expected_rows, "table row limit/mismatch")
        _require(decoded <= limits.max_table_decoded_bytes, "table decoded byte limit")
        end = offset + actual_size + actual_body
    _require(bytes(raw.span(end, footer_start - end)) in (b"", b"\x00" * 4, b"\xff" * 4 + b"\x00" * 4),
             "unexpected data before footer")
    _require(metadata_bytes <= limits.max_ipc_metadata_bytes, "total metadata byte limit")
    _require(rows == expected_rows, "row count mismatch")
    return decoded
