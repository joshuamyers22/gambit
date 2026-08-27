from __future__ import annotations

import gc
import os
import random
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from gambit import _io

CORPUS = Path(__file__).parent / "corpus" / "native_io"
PROBE = Path(__file__).parent / "native_io_probe.py"


def test_native_reader_rejects_missing_file_without_crashing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.csv"

    with pytest.raises(RuntimeError, match="can't read"):
        _io.read_file(str(missing), [0], ["i8"], ",", 0, 0)


def test_native_reader_rejects_empty_schema() -> None:
    with pytest.raises(RuntimeError, match="must not be empty"):
        _io.read_file(str(CORPUS / "invalid_numeric.csv"), [], [], ",", 0, 0)


def test_native_reader_has_safe_default_separator(tmp_path: Path) -> None:
    csv_file = tmp_path / "values.csv"
    csv_file.write_text("42\n")

    (values,) = _io.read_file(str(csv_file), [0], ["i8"], skip_rows=0)

    assert values.tolist() == [42]


def test_native_reader_cleans_up_invalid_string_width(tmp_path: Path) -> None:
    csv_file = tmp_path / "values.csv"
    csv_file.write_text("value\n")

    for _ in range(100):
        with pytest.raises(TypeError, match="item size"):
            _io.read_file(str(csv_file), [0], ["S0"], ",", 0, 0)


def test_native_reader_rejects_invalid_separator(tmp_path: Path) -> None:
    csv_file = tmp_path / "values.csv"
    csv_file.write_text("42\n")

    with pytest.raises(ValueError, match="exactly one byte"):
        _io.read_file(str(csv_file), [0], ["i8"], "", 0, 0)


def test_native_reader_max_rows_excludes_skipped_header(tmp_path: Path) -> None:
    csv_file = tmp_path / "values.csv"
    csv_file.write_text("value\n1\n2\n3\n")

    (values,) = _io.read_file(str(csv_file), [0], ["i8"], ",", 1, 2)

    assert values.tolist() == [1, 2]


def test_native_reader_rejects_missing_selected_field() -> None:
    with pytest.raises(RuntimeError, match="fields on row"):
        _io.read_file(
            str(CORPUS / "missing_column.csv"), [0, 1], ["S16", "f8"], ",", 1, 0
        )


def test_native_reader_allows_unselected_trailing_fields() -> None:
    symbols, prices = _io.read_file(
        str(CORPUS / "extra_column.csv"), [0, 1], ["S16", "f8"], ",", 1, 0
    )

    assert symbols.tolist() == [b"AAPL"]
    assert prices.tolist() == [100.0]


def test_native_reader_invalid_numeric_value_has_documented_nan_semantics() -> None:
    symbols, prices = _io.read_file(
        str(CORPUS / "invalid_numeric.csv"), [0, 1], ["S16", "f8"], ",", 1, 0
    )

    assert symbols.tolist() == [b"AAPL"]
    assert prices.shape == (1,)
    assert np.isnan(prices[0])


def test_native_reader_preserves_long_unterminated_final_row(tmp_path: Path) -> None:
    csv_file = tmp_path / "long-final-row.csv"
    symbol = "A" * (64 * 1024)
    csv_file.write_text(f"{symbol},100.5", encoding="ascii")

    symbols, prices = _io.read_file(str(csv_file), [0, 1], ["S65536", "f8"], ",", 0, 0)

    assert symbols.tolist() == [symbol.encode()]
    assert prices.tolist() == [100.5]


def test_native_reader_rejects_rows_over_input_limit(tmp_path: Path) -> None:
    csv_file = tmp_path / "oversized.csv"
    csv_file.write_bytes(b"A" * (16 * 1024 * 1024 + 1))

    with pytest.raises(RuntimeError, match="16 MiB input limit"):
        _io.read_file(str(csv_file), [0], ["S1"], ",", 0, 0)


def test_native_reader_preserves_non_utf8_bytes(tmp_path: Path) -> None:
    csv_file = tmp_path / "non-utf8.csv"
    csv_file.write_bytes(b"\xff,1.0\n")

    labels, values = _io.read_file(str(csv_file), [0, 1], ["S1", "f8"], ",", 0, 0)

    assert labels.tolist() == [b"\xff"]
    assert values.tolist() == [1.0]


def test_native_reader_reads_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "prices.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("prices.csv", "AAPL,100.5")

    symbols, prices = _io.read_file(
        f"{archive}:prices.csv", [0, 1], ["S16", "f8"], ",", 0, 0
    )

    assert symbols.tolist() == [b"AAPL"]
    assert prices.tolist() == [100.5]


def test_native_zip_reader_does_not_retain_archive_descriptors(tmp_path: Path) -> None:
    descriptor_directory = Path("/dev/fd")
    if not descriptor_directory.is_dir():
        pytest.skip("platform does not expose process file descriptors")
    baseline = len(list(descriptor_directory.iterdir()))

    for index in range(110):
        archive = tmp_path / f"prices-{index}.zip"
        with zipfile.ZipFile(archive, "w") as zip_file:
            zip_file.writestr("prices.csv", "AAPL,100.5")
        _io.read_file(f"{archive}:prices.csv", [0, 1], ["S16", "f8"], ",", 0, 0)

    gc.collect()
    assert len(list(descriptor_directory.iterdir())) <= baseline + 3


@pytest.mark.parametrize("truncate_bytes", [0, 12])
def test_native_reader_rejects_corrupt_or_truncated_zip(
    tmp_path: Path, truncate_bytes: int
) -> None:
    archive = tmp_path / f"broken-{truncate_bytes}.zip"
    if truncate_bytes == 0:
        archive.write_bytes(b"this is not a zip archive")
    else:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("prices.csv", "AAPL,100.5\n")
        archive.write_bytes(archive.read_bytes()[:-truncate_bytes])

    with pytest.raises(RuntimeError, match="can't read"):
        _io.read_file(f"{archive}:prices.csv", [0, 1], ["S16", "f8"], ",", 0, 0)


def test_native_reader_rejects_missing_zip_member(tmp_path: Path) -> None:
    archive = tmp_path / "missing-member.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("other.csv", "AAPL,100.5\n")

    with pytest.raises(RuntimeError, match="can't inspect"):
        _io.read_file(f"{archive}:prices.csv", [0, 1], ["S16", "f8"], ",", 0, 0)


def test_seeded_malformed_inputs_do_not_crash_native_reader(tmp_path: Path) -> None:
    rng = random.Random(20260827)
    alphabet = b"ABCDEF0123456789,\r\n\x00\xff"
    case_files = []

    for case_number in range(24):
        payload = bytes(rng.choice(alphabet) for _ in range(rng.randrange(0, 4096)))
        case_file = tmp_path / f"fuzz-{case_number:02d}.csv"
        case_file.write_bytes(payload)
        case_files.append(case_file)

    result = subprocess.run(
        [sys.executable, str(PROBE), *(str(path) for path in case_files)],
        check=False,
        capture_output=os.environ.get("GAMBIT_SANITIZER_RUN") != "1",
        timeout=10,
    )
    assert result.returncode == 0, (
        "native reader terminated for fuzz seed 20260827: "
        f"stderr={(result.stderr or b'').decode(errors='replace')}"
    )


@pytest.mark.parametrize(
    ("indices", "dtypes", "message"),
    [
        ([1, 0], ["i8", "i8"], "monotonically increasing"),
        ([0], ["i8", "i8"], "same size"),
        ([0], ["object"], "expected i1, i4, i8, f4, f8"),
        ([0], [""], "expected i1, i4, i8, f4, f8"),
    ],
)
def test_native_reader_rejects_invalid_schema(indices, dtypes, message: str) -> None:
    with pytest.raises((RuntimeError, TypeError), match=message):
        _io.read_file(str(CORPUS / "invalid_numeric.csv"), indices, dtypes, ",", 1, 0)
