from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from gambit import _io

CORPUS = Path(__file__).parent / "corpus" / "native_io"


def test_native_reader_rejects_missing_file_without_crashing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.csv"

    with pytest.raises(RuntimeError, match="can't read"):
        _io.read_file(str(missing), [0], ["i8"], ",", 0, 0)


def test_native_reader_rejects_empty_schema() -> None:
    with pytest.raises(RuntimeError, match="must not be empty"):
        _io.read_file(str(CORPUS / "invalid_numeric.csv"), [], [], ",", 0, 0)


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


@pytest.mark.parametrize(
    ("indices", "dtypes", "message"),
    [
        ([1, 0], ["i8", "i8"], "monotonically increasing"),
        ([0], ["i8", "i8"], "same size"),
        ([0], ["object"], "expected i1, i4, i8, f4, f8"),
    ],
)
def test_native_reader_rejects_invalid_schema(indices, dtypes, message: str) -> None:
    with pytest.raises((RuntimeError, TypeError), match=message):
        _io.read_file(str(CORPUS / "invalid_numeric.csv"), indices, dtypes, ",", 1, 0)
