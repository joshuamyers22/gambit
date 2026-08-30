"""Hierarchical exposure limits and persistent pre-trade control state."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

import numpy as np
import polars as pl

from gambit.pq_types import Order
from gambit.risk import PolicyResult, RiskContext


class ControlLevel(str, Enum):
    PORTFOLIO = "portfolio"
    STRATEGY = "strategy"
    GROUP = "group"
    INSTRUMENT = "instrument"


_LEVEL_COLUMN = {
    ControlLevel.STRATEGY: "strategy",
    ControlLevel.GROUP: "contract_group",
    ControlLevel.INSTRUMENT: "symbol",
}


@dataclass(frozen=True)
class ExposureLimit:
    """Maximum gross monetary exposure for one hierarchy node."""

    level: ControlLevel
    maximum: float
    key: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.maximum) or self.maximum <= 0:
            raise ValueError("exposure limit must be finite and positive")
        if self.level is ControlLevel.PORTFOLIO:
            if self.key is not None:
                raise ValueError("portfolio limit key must be None")
        elif not self.key:
            raise ValueError(f"{self.level.value} limit requires a key")


@dataclass(frozen=True)
class ExposureLimitResult:
    positions: pl.DataFrame
    diagnostics: pl.DataFrame

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", self.positions.clone())
        object.__setattr__(self, "diagnostics", self.diagnostics.clone())


@dataclass(frozen=True)
class HierarchicalExposureLimiter:
    """Clip child exposures proportionally at each configured hierarchy node."""

    limits: tuple[ExposureLimit, ...]

    def __init__(self, limits: Sequence[ExposureLimit]) -> None:
        resolved = tuple(limits)
        if not resolved:
            raise ValueError("at least one exposure limit is required")
        identities = [(limit.level, limit.key) for limit in resolved]
        if len(set(identities)) != len(identities):
            raise ValueError("exposure limits must have unique level/key identities")
        object.__setattr__(self, "limits", resolved)

    def apply(self, positions: pl.DataFrame) -> ExposureLimitResult:
        required = {"symbol", "strategy", "contract_group", "net_exposure"}
        if missing := required - set(positions.columns):
            raise ValueError(f"position columns are missing: {', '.join(sorted(missing))}")
        if positions.is_empty():
            raise ValueError("positions cannot be empty")
        for dimension in ("symbol", "strategy", "contract_group"):
            if positions[dimension].null_count():
                raise ValueError(f"position {dimension} cannot be null")
        if positions["net_exposure"].null_count():
            raise ValueError("net exposure cannot be null")
        values = positions["net_exposure"].cast(pl.Float64).to_numpy()
        if not np.isfinite(values).all():
            raise ValueError("net exposure must be finite")

        result = positions.with_columns(
            pl.col("net_exposure").cast(pl.Float64).alias("pre_limit_net_exposure"),
            pl.col("net_exposure").cast(pl.Float64),
            pl.lit(1.0).alias("limit_multiplier"),
        )
        rows: list[dict[str, object]] = []
        ordering = {
            ControlLevel.INSTRUMENT: 0,
            ControlLevel.GROUP: 1,
            ControlLevel.STRATEGY: 2,
            ControlLevel.PORTFOLIO: 3,
        }
        for limit in sorted(self.limits, key=lambda item: ordering[item.level]):
            mask = self._mask(limit)
            matched = result.filter(mask)
            if matched.is_empty():
                raise ValueError(f"exposure limit matches no positions: {limit.level.value}/{limit.key}")
            gross_before = float(matched["net_exposure"].abs().sum())
            multiplier = min(1.0, limit.maximum / gross_before) if gross_before > 0 else 1.0
            result = result.with_columns(
                pl.when(mask)
                .then(pl.col("net_exposure") * multiplier)
                .otherwise(pl.col("net_exposure"))
                .alias("net_exposure"),
                pl.when(mask)
                .then(pl.col("limit_multiplier") * multiplier)
                .otherwise(pl.col("limit_multiplier"))
                .alias("limit_multiplier"),
            )
            rows.append(
                {
                    "level": limit.level.value,
                    "key": limit.key,
                    "gross_before": gross_before,
                    "limit": limit.maximum,
                    "multiplier": multiplier,
                    "gross_after": gross_before * multiplier,
                    "clipped": multiplier < 1.0,
                }
            )
        result = result.with_columns(pl.col("net_exposure").abs().alias("gross_exposure"))
        return ExposureLimitResult(result, pl.DataFrame(rows))

    @staticmethod
    def _mask(limit: ExposureLimit) -> pl.Expr:
        if limit.level is ControlLevel.PORTFOLIO:
            return pl.lit(True)
        return pl.col(_LEVEL_COLUMN[limit.level]) == limit.key


class TradingMode(str, Enum):
    ACTIVE = "active"
    REDUCE_ONLY = "reduce_only"
    NO_TRADE = "no_trade"


@dataclass(frozen=True)
class TradingOverride:
    level: ControlLevel
    mode: TradingMode
    effective_from: np.datetime64
    reason: str
    key: str | None = None
    expires_at: np.datetime64 | None = None

    def __post_init__(self) -> None:
        start: np.datetime64 = np.datetime64(str(self.effective_from)).astype("datetime64[ns]")
        end: np.datetime64 | None = (
            None if self.expires_at is None else np.datetime64(str(self.expires_at)).astype("datetime64[ns]")
        )
        if np.isnat(start) or end is not None and np.isnat(end):
            raise ValueError("override timestamps cannot be NaT")
        if end is not None and end < start:
            raise ValueError("override expiry cannot precede its effective time")
        if not self.reason.strip():
            raise ValueError("override reason cannot be empty")
        if self.level is ControlLevel.PORTFOLIO:
            if self.key is not None:
                raise ValueError("portfolio override key must be None")
        elif not self.key:
            raise ValueError(f"{self.level.value} override requires a key")
        object.__setattr__(self, "effective_from", start)
        object.__setattr__(self, "expires_at", end)

    def is_active(self, timestamp: np.datetime64) -> bool:
        value: np.datetime64 = np.datetime64(str(timestamp)).astype("datetime64[ns]")
        return bool(self.effective_from <= value and (self.expires_at is None or value <= self.expires_at))


@dataclass(frozen=True)
class TradingOverrideBook:
    overrides: tuple[TradingOverride, ...] = ()

    def __init__(self, overrides: Sequence[TradingOverride] = ()) -> None:
        object.__setattr__(self, "overrides", tuple(overrides))

    def resolve(self, order: Order, timestamp: np.datetime64, *, strategy: str | None = None) -> TradingOverride | None:
        matches = [
            override
            for override in self.overrides
            if override.is_active(timestamp) and self._matches(override, order, strategy)
        ]
        if not matches:
            return None
        severity = {TradingMode.ACTIVE: 0, TradingMode.REDUCE_ONLY: 1, TradingMode.NO_TRADE: 2}
        specificity = {
            ControlLevel.PORTFOLIO: 0,
            ControlLevel.STRATEGY: 1,
            ControlLevel.GROUP: 2,
            ControlLevel.INSTRUMENT: 3,
        }
        return max(matches, key=lambda item: (severity[item.mode], specificity[item.level], item.effective_from))

    @staticmethod
    def _matches(override: TradingOverride, order: Order, strategy: str | None) -> bool:
        if override.level is ControlLevel.PORTFOLIO:
            return True
        if override.level is ControlLevel.STRATEGY:
            return strategy == override.key
        if override.level is ControlLevel.GROUP:
            return order.contract.contract_group.name == override.key
        return order.contract.symbol == override.key

    def save(self, path_value: str | Path) -> None:
        path = Path(path_value)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "overrides": [
                {
                    "level": item.level.value,
                    "key": item.key,
                    "mode": item.mode.value,
                    "effective_from": str(item.effective_from),
                    "expires_at": None if item.expires_at is None else str(item.expires_at),
                    "reason": item.reason,
                }
                for item in self.overrides
            ],
        }
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise

    @classmethod
    def load(cls, path_value: str | Path) -> TradingOverrideBook:
        payload = json.loads(Path(path_value).read_text())
        if not isinstance(payload, dict):
            raise ValueError("trading override book root must be an object")
        if payload.get("schema_version") != 1 or not isinstance(payload.get("overrides"), list):
            raise ValueError("unsupported trading override book schema")
        if not all(isinstance(item, dict) for item in payload["overrides"]):
            raise ValueError("trading override entries must be objects")
        return cls(tuple(
            TradingOverride(
                level=ControlLevel(item["level"]),
                key=item.get("key"),
                mode=TradingMode(item["mode"]),
                effective_from=np.datetime64(item["effective_from"]),
                expires_at=None if item.get("expires_at") is None else np.datetime64(item["expires_at"]),
                reason=item["reason"],
            )
            for item in payload["overrides"]
        ))


@dataclass(frozen=True)
class TradingOverridePolicy:
    book: TradingOverrideBook
    strategy: str | None = None
    name: str = "trading_override"

    def evaluate(self, order: Order, context: RiskContext) -> PolicyResult:
        override = self.book.resolve(order, context.timestamp, strategy=self.strategy)
        if override is None or override.mode is TradingMode.ACTIVE:
            return PolicyResult(True)
        if override.mode is TradingMode.NO_TRADE:
            return PolicyResult(False, "no_trade_override", override.reason)
        projected = context.projected_position(order)
        before = projected - order.qty
        if abs(projected) < abs(before):
            return PolicyResult(True)
        return PolicyResult(False, "reduce_only_override", override.reason)


@dataclass(frozen=True)
class RollingTradeBudget:
    """Limit absolute executed plus pending quantity over a trailing window."""

    maximum_quantity: float
    window: np.timedelta64
    level: ControlLevel = ControlLevel.PORTFOLIO
    key: str | None = None
    strategy: str | None = None
    name: str = "rolling_trade_budget"

    def __post_init__(self) -> None:
        if not math.isfinite(self.maximum_quantity) or self.maximum_quantity <= 0:
            raise ValueError("trade budget must be finite and positive")
        unit = np.datetime_data(self.window.dtype)[0]
        if unit in {"Y", "M"}:
            raise ValueError("trade-budget window must use a fixed-duration unit")
        window: np.timedelta64 = np.timedelta64(self.window).astype("timedelta64[ns]")
        if np.isnat(window) or int(window.astype(np.int64)) <= 0:
            raise ValueError("trade-budget window must be positive")
        if self.level is ControlLevel.PORTFOLIO:
            if self.key is not None:
                raise ValueError("portfolio trade-budget key must be None")
        elif self.level is ControlLevel.STRATEGY:
            if not self.key or self.strategy != self.key:
                raise ValueError("strategy trade budget requires a matching strategy key")
        elif not self.key:
            raise ValueError(f"{self.level.value} trade budget requires a key")
        object.__setattr__(self, "window", window)

    def evaluate(self, order: Order, context: RiskContext) -> PolicyResult:
        start = context.timestamp - self.window
        executed = sum(
            abs(trade.qty)
            for trade in context.account.trades(start_date=start, end_date=context.timestamp)
            if self._matches(trade.order)
        )
        pending = sum(
            abs(pending_order.qty)
            for pending_order in context.open_orders
            if pending_order.is_open() and self._matches(pending_order)
        )
        projected = executed + pending + abs(order.qty)
        if projected > self.maximum_quantity:
            return PolicyResult(
                False,
                "rolling_trade_budget_exceeded",
                f"rolling absolute quantity {projected:g} exceeds {self.maximum_quantity:g}",
            )
        return PolicyResult(True)

    def _matches(self, order: Order) -> bool:
        if self.level in {ControlLevel.PORTFOLIO, ControlLevel.STRATEGY}:
            return True
        if self.level is ControlLevel.GROUP:
            return order.contract.contract_group.name == self.key
        return order.contract.symbol == self.key
