"""Validation utilities for market-data frames."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

import numpy as np
import pandas_market_calendars as mcal
import polars as pl


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: ValidationSeverity
    message: str
    count: int


@dataclass(frozen=True)
class MarketDataValidationReport:
    findings: tuple[ValidationFinding, ...]

    @property
    def is_valid(self) -> bool:
        return not any(finding.severity is ValidationSeverity.ERROR for finding in self.findings)

    def by_code(self, code: str) -> ValidationFinding | None:
        return next((finding for finding in self.findings if finding.code == code), None)

    def raise_if_invalid(self) -> None:
        if not self.is_valid:
            details = "; ".join(f"{finding.code}: {finding.message}" for finding in self.findings)
            raise ValueError(f"invalid market data: {details}")


def validate_market_data(
    data: pl.DataFrame,
    *,
    timestamp_column: str = "timestamp",
    price_columns: Sequence[str] = ("price",),
    volume_columns: Sequence[str] = (),
    calendar_name: str | None = None,
    reject_future_timestamps: bool = True,
    max_price_change: float | None = None,
    now: np.datetime64 | None = None,
) -> MarketDataValidationReport:
    """Inspect a Polars frame without modifying it.

    ``max_price_change`` is the maximum allowed absolute fractional change
    between adjacent non-null observations in each price column.
    """
    findings: list[ValidationFinding] = []
    required_columns = (timestamp_column, *price_columns, *volume_columns)
    missing = [column for column in required_columns if column not in data.columns]
    if missing:
        findings.append(
            ValidationFinding(
                "missing_columns",
                ValidationSeverity.ERROR,
                f"required columns are missing: {', '.join(missing)}",
                len(missing),
            )
        )
        return MarketDataValidationReport(tuple(findings))

    timestamp_dtype = data.schema[timestamp_column]
    if timestamp_dtype != pl.Date and not isinstance(timestamp_dtype, pl.Datetime):
        findings.append(
            ValidationFinding(
                "invalid_timestamp_type",
                ValidationSeverity.ERROR,
                f"{timestamp_column} must be a Polars Date or Datetime, got {timestamp_dtype}",
                data.height,
            )
        )
        return MarketDataValidationReport(tuple(findings))

    null_timestamps = data[timestamp_column].null_count()
    if null_timestamps:
        findings.append(
            ValidationFinding("null_timestamps", ValidationSeverity.ERROR, "timestamps contain null values", null_timestamps)
        )

    duplicate_timestamps = data.select(pl.col(timestamp_column).is_duplicated().sum()).item()
    if duplicate_timestamps:
        findings.append(
            ValidationFinding(
                "duplicate_timestamps",
                ValidationSeverity.ERROR,
                "timestamps are not unique",
                duplicate_timestamps,
            )
        )

    if not data[timestamp_column].is_sorted():
        findings.append(
            ValidationFinding("unordered_timestamps", ValidationSeverity.ERROR, "timestamps are not sorted", data.height)
        )

    if reject_future_timestamps and not null_timestamps and data.height:
        cutoff = now if now is not None else np.datetime64("now", "us")
        future_count = data.select((pl.col(timestamp_column) > cutoff).sum()).item()
        if future_count:
            findings.append(
                ValidationFinding(
                    "future_timestamps", ValidationSeverity.ERROR, "timestamps occur after the validation time", future_count
                )
            )

    for column in price_columns:
        null_count = data[column].null_count()
        non_finite = data.select((~pl.col(column).is_finite()).fill_null(False).sum()).item()
        non_positive = data.select((pl.col(column) <= 0).fill_null(False).sum()).item()
        if null_count:
            findings.append(
                ValidationFinding("null_prices", ValidationSeverity.ERROR, f"{column} contains null prices", null_count)
            )
        if non_finite:
            findings.append(
                ValidationFinding(
                    "non_finite_prices", ValidationSeverity.ERROR, f"{column} contains non-finite prices", non_finite
                )
            )
        if non_positive:
            findings.append(
                ValidationFinding(
                    "non_positive_prices", ValidationSeverity.ERROR, f"{column} contains non-positive prices", non_positive
                )
            )
        if max_price_change is not None:
            if max_price_change <= 0:
                raise ValueError("max_price_change must be positive")
            spike_count = data.select(
                (pl.col(column).pct_change().abs() > max_price_change).fill_null(False).sum()
            ).item()
            if spike_count:
                findings.append(
                    ValidationFinding(
                        "price_spikes",
                        ValidationSeverity.WARNING,
                        f"{column} exceeds the configured adjacent price-change threshold",
                        spike_count,
                    )
                )

    for column in volume_columns:
        negative_count = data.select((pl.col(column) < 0).fill_null(False).sum()).item()
        zero_count = data.select((pl.col(column) == 0).fill_null(False).sum()).item()
        if negative_count:
            findings.append(
                ValidationFinding(
                    "negative_volume", ValidationSeverity.ERROR, f"{column} contains negative volume", negative_count
                )
            )
        if zero_count:
            findings.append(
                ValidationFinding("zero_volume", ValidationSeverity.WARNING, f"{column} contains zero volume", zero_count)
            )

    if calendar_name is not None and data.height and not null_timestamps:
        dates = data[timestamp_column].dt.date().unique().to_list()
        calendar = mcal.get_calendar(calendar_name)
        valid_dates = {date.date() for date in calendar.valid_days(min(dates), max(dates))}
        invalid_sessions = sum(date not in valid_dates for date in dates)
        if invalid_sessions:
            findings.append(
                ValidationFinding(
                    "non_trading_sessions",
                    ValidationSeverity.ERROR,
                    f"dates are not sessions in the {calendar_name} calendar",
                    invalid_sessions,
                )
            )

    return MarketDataValidationReport(tuple(findings))
