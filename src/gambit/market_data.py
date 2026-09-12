"""Validation utilities for market-data frames."""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType, SimpleNamespace
from typing import Any, Sequence, cast

import numpy as np
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


class MarketDataAvailabilityPolicy(str, Enum):
    """Behavior when a point-in-time observation is missing or stale."""

    ERROR = "error"
    WARN = "warn"
    SKIP = "skip"


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

    if data.is_empty():
        findings.append(
            ValidationFinding("empty_data", ValidationSeverity.ERROR, "market data contains no observations", 0)
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
        if not data.schema[column].is_numeric():
            findings.append(
                ValidationFinding(
                    "invalid_price_type",
                    ValidationSeverity.ERROR,
                    f"{column} must be numeric, got {data.schema[column]}",
                    data.height,
                )
            )
            continue
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
        if not data.schema[column].is_numeric():
            findings.append(
                ValidationFinding(
                    "invalid_volume_type",
                    ValidationSeverity.ERROR,
                    f"{column} must be numeric, got {data.schema[column]}",
                    data.height,
                )
            )
            continue
        null_count = data[column].null_count()
        non_finite = data.select((~pl.col(column).is_finite()).fill_null(False).sum()).item()
        negative_count = data.select((pl.col(column) < 0).fill_null(False).sum()).item()
        zero_count = data.select((pl.col(column) == 0).fill_null(False).sum()).item()
        if null_count:
            findings.append(
                ValidationFinding("null_volume", ValidationSeverity.ERROR, f"{column} contains null volume", null_count)
            )
        if non_finite:
            findings.append(
                ValidationFinding(
                    "non_finite_volume", ValidationSeverity.ERROR, f"{column} contains non-finite volume", non_finite
                )
            )
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
        try:
            import pandas_market_calendars as mcal
        except ImportError as exc:  # pragma: no cover - exercised in minimal-install CI
            raise ImportError("calendar validation requires 'gambit-markets[calendars]'") from exc
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


_POINT_IN_TIME_COLUMNS = (
    "symbol",
    "field",
    "observation_time",
    "available_time",
    "revision",
    "value",
)


def _point_in_time_timestamp(value: np.datetime64, *, label: str) -> np.datetime64:
    if not isinstance(value, np.datetime64):
        raise TypeError(f"{label} must be a numpy datetime64 value")
    if np.isnat(value):
        raise ValueError(f"{label} cannot be NaT")
    return cast(np.datetime64, value.astype("datetime64[ns]"))


def _maximum_age_ns(value: np.timedelta64 | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, np.timedelta64):
        raise TypeError("maximum age must be a numpy timedelta64 value or None")
    if np.isnat(value):
        raise ValueError("maximum age cannot be NaT")
    nanoseconds = int(value.astype("timedelta64[ns]").astype(np.int64))
    if nanoseconds <= 0:
        raise ValueError("maximum age must be positive")
    return nanoseconds


def _unavailable_observation(
    message: str,
    policy: MarketDataAvailabilityPolicy,
) -> None:
    if not isinstance(policy, MarketDataAvailabilityPolicy):
        raise TypeError(
            "market-data availability policies must be MarketDataAvailabilityPolicy values"
        )
    if policy is MarketDataAvailabilityPolicy.ERROR:
        raise LookupError(message)
    if policy is MarketDataAvailabilityPolicy.WARN:
        warnings.warn(message, RuntimeWarning, stacklevel=3)


def _fingerprint_point_in_time_frame(frame: pl.DataFrame) -> str:
    schema = [(name, str(dtype)) for name, dtype in frame.schema.items()]
    row_hashes = frame.hash_rows(seed=0, seed_1=1, seed_2=2, seed_3=3).to_numpy().tobytes()
    digest = hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode())
    digest.update(row_hashes)
    return digest.hexdigest()


@dataclass(frozen=True)
class PointInTimeObservation:
    """One numeric observation selected as it was knowable at a heartbeat."""

    symbol: str
    field: str
    observation_time: np.datetime64
    available_time: np.datetime64
    revision: str
    value: float


@dataclass(frozen=True, init=False)
class PointInTimeMarketData:
    """Immutable, revision-aware numeric data with causal scalar/window reads.

    Rows use the exact columns ``symbol``, ``field``, ``observation_time``,
    ``available_time``, ``revision``, and ``value``. Multiple revisions of an
    observation have distinct availability times; a read selects the latest
    revision available at its ``as_of`` heartbeat.
    """

    source: str
    dataset_revision: str
    _frame: pl.DataFrame = field(init=False, repr=False, compare=False)
    fingerprint: str = field(init=False)

    def __init__(self, frame: pl.DataFrame, *, source: str, dataset_revision: str) -> None:
        if not isinstance(frame, pl.DataFrame):
            raise TypeError("point-in-time market data must be a Polars DataFrame")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("market-data source must be a non-empty string")
        if not isinstance(dataset_revision, str) or not dataset_revision.strip():
            raise ValueError("market-data dataset_revision must be a non-empty string")
        if set(frame.columns) != set(_POINT_IN_TIME_COLUMNS):
            raise ValueError(
                "point-in-time market data must contain exactly: "
                + ", ".join(_POINT_IN_TIME_COLUMNS)
            )
        for column in ("symbol", "field", "revision"):
            if frame.schema[column] != pl.String:
                raise TypeError(f"point-in-time {column} must be a Polars String column")
        for column in ("observation_time", "available_time"):
            dtype = frame.schema[column]
            if dtype != pl.Date and not isinstance(dtype, pl.Datetime):
                raise TypeError(f"point-in-time {column} must be a Polars Date or Datetime column")
            if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
                raise ValueError(
                    f"point-in-time {column} must be normalized to a timezone-naive time basis"
                )
        if not frame.schema["value"].is_numeric():
            raise TypeError("point-in-time value must be a numeric column")

        normalized = frame.select(
            pl.col("symbol"),
            pl.col("field"),
            pl.col("observation_time").cast(pl.Datetime("ns")),
            pl.col("available_time").cast(pl.Datetime("ns")),
            pl.col("revision"),
            pl.col("value").cast(pl.Float64),
        )
        if any(normalized[column].null_count() for column in _POINT_IN_TIME_COLUMNS):
            raise ValueError("point-in-time market data cannot contain null values")
        for column in ("symbol", "field", "revision"):
            if normalized.select((pl.col(column).str.len_chars() == 0).any()).item():
                raise ValueError(f"point-in-time {column} values must be non-empty")
        if normalized.select((~pl.col("value").is_finite()).any()).item():
            raise ValueError("point-in-time values must be finite")
        if normalized.select((pl.col("available_time") < pl.col("observation_time")).any()).item():
            raise ValueError("point-in-time availability cannot precede observation time")
        identity_columns = ["symbol", "field", "observation_time", "available_time"]
        if normalized.select(pl.struct(identity_columns).is_duplicated().any()).item():
            raise ValueError("point-in-time observations cannot share an availability identity")

        normalized = normalized.sort([*identity_columns, "revision"])
        frame_fingerprint = _fingerprint_point_in_time_frame(normalized)
        identity = json.dumps(
            {
                "format": "gambit.point-in-time-market-data",
                "version": 1,
                "source": source,
                "dataset_revision": dataset_revision,
                "frame_sha256": frame_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "dataset_revision", dataset_revision)
        object.__setattr__(self, "_frame", normalized)
        object.__setattr__(self, "fingerprint", hashlib.sha256(identity).hexdigest())

    def _eligible(
        self,
        symbol: str,
        field_name: str,
        start: np.datetime64,
        end: np.datetime64,
        as_of: np.datetime64,
    ) -> pl.DataFrame:
        return (
            self._frame.filter(
                (pl.col("symbol") == symbol)
                & (pl.col("field") == field_name)
                & (pl.col("observation_time") >= start)
                & (pl.col("observation_time") <= end)
                & (pl.col("available_time") <= as_of)
            )
            .sort(["observation_time", "available_time", "revision"])
            .unique(subset=["observation_time"], keep="last", maintain_order=True)
            .sort("observation_time")
        )

    def read(
        self,
        symbol: str,
        field_name: str,
        observation_time: np.datetime64,
        *,
        as_of: np.datetime64,
        allow_previous: bool = False,
        max_age: np.timedelta64 | None = None,
        missing_policy: MarketDataAvailabilityPolicy = MarketDataAvailabilityPolicy.ERROR,
        stale_policy: MarketDataAvailabilityPolicy = MarketDataAvailabilityPolicy.ERROR,
    ) -> PointInTimeObservation | None:
        """Read one exact or prior observation without crossing ``as_of``."""
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("market-data symbol must be a non-empty string")
        if not isinstance(field_name, str) or not field_name:
            raise ValueError("market-data field must be a non-empty string")
        if type(allow_previous) is not bool:
            raise TypeError("allow_previous must be a bool")
        for policy in (missing_policy, stale_policy):
            if not isinstance(policy, MarketDataAvailabilityPolicy):
                raise TypeError(
                    "market-data availability policies must be MarketDataAvailabilityPolicy values"
                )
        requested = _point_in_time_timestamp(observation_time, label="observation_time")
        heartbeat = _point_in_time_timestamp(as_of, label="as_of")
        if requested > heartbeat:
            raise ValueError("observation_time cannot be after the as_of heartbeat")
        maximum_age = _maximum_age_ns(max_age)
        start = requested
        if allow_previous and self._frame.height:
            start = cast(np.datetime64, self._frame["observation_time"].min())
        eligible = self._eligible(symbol, field_name, start, requested, heartbeat)
        if eligible.is_empty():
            _unavailable_observation(
                f"no {field_name} observation for {symbol} is available at {heartbeat}",
                missing_policy,
            )
            return None
        row = eligible.row(-1, named=True)
        selected_time = _point_in_time_timestamp(
            np.datetime64(row["observation_time"]), label="selected observation_time"
        )
        if maximum_age is not None:
            age = int((heartbeat - selected_time).astype("timedelta64[ns]").astype(np.int64))
            if age > maximum_age:
                _unavailable_observation(
                    f"{field_name} observation for {symbol} at {selected_time} is stale at {heartbeat}",
                    stale_policy,
                )
                return None
        return PointInTimeObservation(
            symbol=cast(str, row["symbol"]),
            field=cast(str, row["field"]),
            observation_time=selected_time,
            available_time=_point_in_time_timestamp(
                np.datetime64(row["available_time"]), label="selected available_time"
            ),
            revision=cast(str, row["revision"]),
            value=float(row["value"]),
        )

    def read_window(
        self,
        symbol: str,
        field_name: str,
        start: np.datetime64,
        end: np.datetime64,
        *,
        as_of: np.datetime64,
        missing_policy: MarketDataAvailabilityPolicy = MarketDataAvailabilityPolicy.ERROR,
    ) -> pl.DataFrame:
        """Return the latest available revision per observation in an inclusive window."""
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("market-data symbol must be a non-empty string")
        if not isinstance(field_name, str) or not field_name:
            raise ValueError("market-data field must be a non-empty string")
        if not isinstance(missing_policy, MarketDataAvailabilityPolicy):
            raise TypeError(
                "market-data availability policies must be MarketDataAvailabilityPolicy values"
            )
        start_time = _point_in_time_timestamp(start, label="window start")
        end_time = _point_in_time_timestamp(end, label="window end")
        heartbeat = _point_in_time_timestamp(as_of, label="as_of")
        if start_time > end_time:
            raise ValueError("market-data window start cannot be after end")
        if end_time > heartbeat:
            raise ValueError("market-data window end cannot be after the as_of heartbeat")
        eligible = self._eligible(symbol, field_name, start_time, end_time, heartbeat)
        if eligible.is_empty():
            _unavailable_observation(
                f"no {field_name} observations for {symbol} are available at {heartbeat}",
                missing_policy,
            )
        return eligible.clone()


@dataclass(frozen=True)
class PointInTimePriceFunction:
    """Strategy price adapter backed by a causal point-in-time dataset."""

    data: PointInTimeMarketData
    field_name: str = "price"
    allow_previous: bool = False
    max_age: np.timedelta64 | None = None
    missing_policy: MarketDataAvailabilityPolicy = MarketDataAvailabilityPolicy.ERROR
    stale_policy: MarketDataAvailabilityPolicy = MarketDataAvailabilityPolicy.ERROR
    provenance_name: str = "market_data"

    def __post_init__(self) -> None:
        if not isinstance(self.data, PointInTimeMarketData):
            raise TypeError("data must be PointInTimeMarketData")
        if not isinstance(self.field_name, str) or not self.field_name:
            raise ValueError("price field_name must be a non-empty string")
        if type(self.allow_previous) is not bool:
            raise TypeError("allow_previous must be a bool")
        _maximum_age_ns(self.max_age)
        for policy in (self.missing_policy, self.stale_policy):
            if not isinstance(policy, MarketDataAvailabilityPolicy):
                raise TypeError("price availability policies must be MarketDataAvailabilityPolicy values")
        if not isinstance(self.provenance_name, str) or not self.provenance_name:
            raise ValueError("provenance_name must be a non-empty string")

    @property
    def input_fingerprints(self) -> Mapping[str, str]:
        return MappingProxyType({self.provenance_name: self.data.fingerprint})

    def __call__(
        self,
        contract: Any,
        timestamps: np.ndarray,
        index: int,
        _context: SimpleNamespace | None,
    ) -> float:
        heartbeat = _point_in_time_timestamp(
            cast(np.datetime64, timestamps[index]), label="strategy heartbeat"
        )

        def price(component: Any) -> float:
            observation = self.data.read(
                component.symbol,
                self.field_name,
                heartbeat,
                as_of=heartbeat,
                allow_previous=self.allow_previous,
                max_age=self.max_age,
                missing_policy=self.missing_policy,
                stale_policy=self.stale_policy,
            )
            return math.nan if observation is None else observation.value

        if contract.is_basket():
            return sum(price(component) * ratio for component, ratio in contract.components)
        return price(contract)


@dataclass(frozen=True)
class PointInTimeIndicator:
    """Indicator stage that resolves each heartbeat through an owned dataset."""

    data: PointInTimeMarketData
    symbol: str
    field_name: str = "price"
    allow_previous: bool = False
    max_age: np.timedelta64 | None = None
    missing_policy: MarketDataAvailabilityPolicy = MarketDataAvailabilityPolicy.ERROR
    stale_policy: MarketDataAvailabilityPolicy = MarketDataAvailabilityPolicy.ERROR
    provenance_name: str = "market_data"

    def __post_init__(self) -> None:
        if not isinstance(self.data, PointInTimeMarketData):
            raise TypeError("data must be PointInTimeMarketData")
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("indicator symbol must be a non-empty string")
        if not isinstance(self.field_name, str) or not self.field_name:
            raise ValueError("indicator field_name must be a non-empty string")
        if type(self.allow_previous) is not bool:
            raise TypeError("allow_previous must be a bool")
        _maximum_age_ns(self.max_age)
        for policy in (self.missing_policy, self.stale_policy):
            if not isinstance(policy, MarketDataAvailabilityPolicy):
                raise TypeError("indicator availability policies must be MarketDataAvailabilityPolicy values")
        if not isinstance(self.provenance_name, str) or not self.provenance_name:
            raise ValueError("provenance_name must be a non-empty string")

    @property
    def input_fingerprints(self) -> Mapping[str, str]:
        return MappingProxyType({self.provenance_name: self.data.fingerprint})

    def __call__(
        self,
        _contract_group: Any,
        timestamps: np.ndarray,
        _parent_values: SimpleNamespace,
        _strategy_context: SimpleNamespace,
    ) -> np.ndarray:
        values: np.ndarray = np.empty(len(timestamps), dtype=float)
        for index, timestamp in enumerate(timestamps):
            heartbeat = _point_in_time_timestamp(
                cast(np.datetime64, timestamp), label="indicator heartbeat"
            )
            observation = self.data.read(
                self.symbol,
                self.field_name,
                heartbeat,
                as_of=heartbeat,
                allow_previous=self.allow_previous,
                max_age=self.max_age,
                missing_policy=self.missing_policy,
                stale_policy=self.stale_policy,
            )
            values[index] = math.nan if observation is None else observation.value
        return values


__all__ = [
    "MarketDataAvailabilityPolicy",
    "MarketDataValidationReport",
    "PointInTimeIndicator",
    "PointInTimeMarketData",
    "PointInTimeObservation",
    "PointInTimePriceFunction",
    "ValidationFinding",
    "ValidationSeverity",
    "validate_market_data",
]
