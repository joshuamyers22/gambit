import datetime as dt

import numpy as np
import polars as pl
import pytest

from gambit.market_data import ValidationSeverity, validate_market_data


def test_valid_market_data_has_no_findings() -> None:
    data = pl.DataFrame(
        {
            "timestamp": [dt.datetime(2024, 1, 2), dt.datetime(2024, 1, 3)],
            "price": [100.0, 101.0],
            "volume": [10.0, 20.0],
        }
    )

    report = validate_market_data(data, volume_columns=("volume",), calendar_name="NYSE")

    assert report.is_valid
    assert report.findings == ()


def test_validation_reports_quality_problems_without_mutating_data() -> None:
    data = pl.DataFrame(
        {
            "timestamp": [dt.datetime(2024, 1, 3), dt.datetime(2024, 1, 2), dt.datetime(2024, 1, 2)],
            "price": [100.0, 0.0, np.inf],
            "volume": [10.0, 0.0, -1.0],
        }
    )
    original = data.clone()

    report = validate_market_data(data, volume_columns=("volume",), max_price_change=0.5)

    assert not report.is_valid
    assert {finding.code for finding in report.findings} >= {
        "duplicate_timestamps",
        "unordered_timestamps",
        "non_finite_prices",
        "non_positive_prices",
        "zero_volume",
        "negative_volume",
    }
    assert report.by_code("zero_volume").severity is ValidationSeverity.WARNING
    assert data.equals(original)


def test_calendar_validation_rejects_weekends() -> None:
    data = pl.DataFrame({"timestamp": [dt.datetime(2024, 1, 6)], "price": [100.0]})

    report = validate_market_data(data, calendar_name="NYSE")

    assert report.by_code("non_trading_sessions").count == 1
    with pytest.raises(ValueError, match="non_trading_sessions"):
        report.raise_if_invalid()


def test_future_validation_accepts_explicit_clock() -> None:
    data = pl.DataFrame({"timestamp": [dt.datetime(2024, 1, 3)], "price": [100.0]})

    report = validate_market_data(data, now=np.datetime64("2024-01-02"))

    assert report.by_code("future_timestamps").count == 1


def test_missing_columns_short_circuits_schema_checks() -> None:
    report = validate_market_data(pl.DataFrame({"timestamp": [dt.datetime(2024, 1, 2)]}))

    assert report.by_code("missing_columns").count == 1


def test_non_numeric_market_columns_produce_schema_findings() -> None:
    data = pl.DataFrame(
        {
            "timestamp": [dt.datetime(2024, 1, 2)],
            "price": ["100.0"],
            "volume": ["10"],
        }
    )

    report = validate_market_data(data, volume_columns=("volume",))

    assert report.by_code("invalid_price_type").count == 1
    assert report.by_code("invalid_volume_type").count == 1


def test_null_and_non_finite_volume_are_rejected() -> None:
    data = pl.DataFrame(
        {
            "timestamp": [dt.datetime(2024, 1, 2), dt.datetime(2024, 1, 3)],
            "price": [100.0, 101.0],
            "volume": [None, np.inf],
        },
        schema_overrides={"volume": pl.Float64},
    )

    report = validate_market_data(data, volume_columns=("volume",))

    assert report.by_code("null_volume").count == 1
    assert report.by_code("non_finite_volume").count == 1
