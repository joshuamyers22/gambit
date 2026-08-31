"""Validation contracts at user callback and backtest execution boundaries."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


class BacktestCallbackError(RuntimeError):
    """Add stable execution context while retaining the original exception cause."""


def validate_strategy_timestamps(timestamps: np.ndarray) -> None:
    """Require a non-empty, one-dimensional, strictly increasing datetime grid."""
    if not isinstance(timestamps, np.ndarray):
        raise TypeError("strategy timestamps must be a NumPy array")
    if timestamps.ndim != 1:
        raise ValueError("strategy timestamps must be one-dimensional")
    if not np.issubdtype(timestamps.dtype, np.datetime64):
        raise TypeError("strategy timestamps must have a datetime64 dtype")
    if timestamps.size == 0:
        raise ValueError("strategy timestamps cannot be empty")
    normalized: np.ndarray = timestamps.astype("datetime64[ns]")
    if np.isnat(normalized).any():
        raise ValueError("strategy timestamps cannot contain NaT")
    if not bool(np.all(np.diff(normalized.astype(np.int64)) > 0)):
        raise ValueError("strategy timestamps must be strictly increasing and unique")


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
