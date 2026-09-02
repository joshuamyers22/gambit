"""Validation contracts at user callback and backtest execution boundaries."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


class BacktestCallbackError(RuntimeError):
    """Add stable execution context while retaining the original exception cause."""


def validate_timestamp_grid(timestamps: np.ndarray, *, owner: str) -> None:
    """Require a non-empty, one-dimensional, strictly increasing datetime grid."""
    if not isinstance(timestamps, np.ndarray):
        raise TypeError(f"{owner} timestamps must be a NumPy array")
    if timestamps.ndim != 1:
        raise ValueError(f"{owner} timestamps must be one-dimensional")
    if not np.issubdtype(timestamps.dtype, np.datetime64):
        raise TypeError(f"{owner} timestamps must have a datetime64 dtype")
    if timestamps.size == 0:
        raise ValueError(f"{owner} timestamps cannot be empty")
    normalized: np.ndarray = timestamps.astype("datetime64[ns]")
    if np.isnat(normalized).any():
        raise ValueError(f"{owner} timestamps cannot contain NaT")
    if not bool(np.all(np.diff(normalized.astype(np.int64)) > 0)):
        raise ValueError(f"{owner} timestamps must be strictly increasing and unique")


def validate_strategy_timestamps(timestamps: np.ndarray) -> None:
    validate_timestamp_grid(timestamps, owner="strategy")


def timestamp_index(timestamps: np.ndarray, timestamp: np.datetime64, *, owner: str) -> int:
    """Resolve an exact timestamp without leaking an array bounds error."""
    index = int(np.searchsorted(timestamps, timestamp))
    if index >= len(timestamps) or timestamps[index] != timestamp:
        raise ValueError(f"timestamp {timestamp} is not present in the {owner} timestamp grid")
    return index


def validate_price_value(
    value: Any,
    *,
    symbol: str,
    timestamp: np.datetime64,
    source: str,
    allow_missing: bool = True,
) -> float:
    """Coerce a real scalar price and distinguish missing from invalid values."""
    if isinstance(value, (bool, np.bool_)) or not isinstance(
        value, (int, float, np.integer, np.floating)
    ):
        raise TypeError(
            f"{source} returned a non-real scalar price for {symbol} at {timestamp}: {value!r}"
        )
    price = float(value)
    if math.isnan(price):
        if allow_missing:
            return price
        raise ValueError(f"{source} returned a missing price for {symbol} at {timestamp}")
    if not math.isfinite(price):
        raise ValueError(f"{source} returned a non-finite price for {symbol} at {timestamp}: {price}")
    return price
