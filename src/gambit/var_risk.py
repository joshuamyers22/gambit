"""Point-in-time historical and Gaussian portfolio tail risk."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

import numpy as np
import polars as pl
from numpy.typing import NDArray
from scipy.stats import norm

from gambit.covariance_risk import _datetime_ns, _require_single_currency
from gambit.risk_measures import _measure_rows


class TailRiskMethod(str, Enum):
    HISTORICAL = "historical"
    GAUSSIAN = "gaussian"


@dataclass(frozen=True)
class TailRiskEstimate:
    """Positive loss amounts at one confidence and holding period."""

    value_at_risk: float
    expected_shortfall: float
    confidence: float
    horizon_days: int
    method: TailRiskMethod
    currency: str
    as_of: np.datetime64
    observations: int

    def __post_init__(self) -> None:
        values = (self.value_at_risk, self.expected_shortfall)
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise ValueError("tail-risk loss amounts must be finite and non-negative")
        if self.expected_shortfall + 1e-12 < self.value_at_risk:
            raise ValueError("expected shortfall cannot be below value at risk")
        if not 0.5 < self.confidence < 1:
            raise ValueError("tail-risk confidence must be in (0.5, 1)")
        if isinstance(self.horizon_days, bool) or not isinstance(self.horizon_days, int) or self.horizon_days < 1:
            raise ValueError("tail-risk horizon must be a positive integer")
        if isinstance(self.observations, bool) or not isinstance(self.observations, int) or self.observations < 1:
            raise ValueError("tail-risk observations must be a positive integer")
        currency = self.currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("tail-risk currency must be a three-letter alphabetic code")
        as_of = _datetime_ns(self.as_of)
        if np.isnat(as_of):
            raise ValueError("tail-risk as_of cannot be NaT")
        object.__setattr__(self, "method", TailRiskMethod(self.method))
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "as_of", as_of)


@dataclass(frozen=True)
class FittedTailRiskModel:
    """Aligned, immutable return sample and explicit tail-model policy."""

    symbols: tuple[str, ...]
    returns: NDArray[np.float64]
    as_of: np.datetime64
    confidence: float
    horizon_days: int
    method: TailRiskMethod

    def __post_init__(self) -> None:
        symbols = tuple(self.symbols)
        values = np.asarray(self.returns, dtype=float).copy()
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValueError("tail-risk symbols must be non-empty and unique")
        if values.ndim != 2 or values.shape[1] != len(symbols) or values.shape[0] < 2:
            raise ValueError("tail-risk return matrix shape must match symbols and contain two rows")
        if not np.isfinite(values).all():
            raise ValueError("tail-risk returns must be finite")
        if not 0.5 < self.confidence < 1:
            raise ValueError("tail-risk confidence must be in (0.5, 1)")
        if isinstance(self.horizon_days, bool) or not isinstance(self.horizon_days, int) or self.horizon_days < 1:
            raise ValueError("tail-risk horizon must be a positive integer")
        method = TailRiskMethod(self.method)
        if method is TailRiskMethod.HISTORICAL and values.shape[0] < self.horizon_days:
            raise ValueError("historical tail risk requires at least horizon_days observations")
        as_of = _datetime_ns(self.as_of)
        if np.isnat(as_of):
            raise ValueError("tail-risk as_of cannot be NaT")
        values.setflags(write=False)
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "returns", values)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "method", method)

    def evaluate(self, exposures: pl.DataFrame) -> TailRiskEstimate:
        currency = _require_single_currency(exposures)
        vector = self._exposure_vector(exposures)
        pnl = self.returns @ vector
        if self.method is TailRiskMethod.HISTORICAL:
            losses = -np.convolve(pnl, np.ones(self.horizon_days), mode="valid")
            raw_var = float(np.quantile(losses, self.confidence, method="higher"))
            tail = losses[losses >= raw_var]
            raw_es = float(tail.mean())
            observations = losses.size
        else:
            mean_loss = -float(pnl.mean()) * self.horizon_days
            sigma = float(pnl.std(ddof=1)) * math.sqrt(self.horizon_days)
            quantile = float(norm.ppf(self.confidence))
            raw_var = mean_loss + quantile * sigma
            raw_es = mean_loss + sigma * float(norm.pdf(quantile)) / (1 - self.confidence)
            observations = pnl.size
        value_at_risk = max(raw_var, 0.0)
        expected_shortfall = max(raw_es, value_at_risk)
        return TailRiskEstimate(
            value_at_risk,
            expected_shortfall,
            self.confidence,
            self.horizon_days,
            self.method,
            currency,
            self.as_of,
            int(observations),
        )

    def _exposure_vector(self, exposures: pl.DataFrame) -> NDArray[np.float64]:
        required = {"symbol", "net_exposure"}
        if missing := required - set(exposures.columns):
            raise ValueError(f"exposure columns are missing: {', '.join(sorted(missing))}")
        grouped = exposures.group_by("symbol").agg(pl.col("net_exposure").sum())
        values = dict(grouped.select("symbol", "net_exposure").iter_rows())
        unknown = set(values) - set(self.symbols)
        if unknown:
            raise ValueError(f"tail-risk model is missing symbols: {', '.join(sorted(unknown))}")
        vector = np.asarray([float(values.get(symbol, 0.0)) for symbol in self.symbols])
        if not np.isfinite(vector).all():
            raise ValueError("net exposures must be finite")
        return vector


@dataclass(frozen=True)
class TailRiskModel:
    """Fit a complete-case wide return sample through an explicit cutoff."""

    lookback: int = 252
    min_observations: int = 60
    confidence: float = 0.99
    horizon_days: int = 1
    method: TailRiskMethod = TailRiskMethod.HISTORICAL

    def __post_init__(self) -> None:
        if isinstance(self.lookback, bool) or not isinstance(self.lookback, int) or self.lookback < 2:
            raise ValueError("tail-risk lookback must be at least two")
        if (
            isinstance(self.min_observations, bool)
            or not isinstance(self.min_observations, int)
            or self.min_observations < 2
            or self.min_observations > self.lookback
        ):
            raise ValueError("tail-risk minimum observations must be between two and lookback")
        if not 0.5 < self.confidence < 1:
            raise ValueError("tail-risk confidence must be in (0.5, 1)")
        if isinstance(self.horizon_days, bool) or not isinstance(self.horizon_days, int) or self.horizon_days < 1:
            raise ValueError("tail-risk horizon must be a positive integer")
        object.__setattr__(self, "method", TailRiskMethod(self.method))

    def fit(
        self,
        returns: pl.DataFrame,
        *,
        timestamp_column: str = "timestamp",
        symbols: Sequence[str] | None = None,
        as_of: np.datetime64 | None = None,
    ) -> FittedTailRiskModel:
        if timestamp_column not in returns.columns:
            raise ValueError(f"timestamp column is missing: {timestamp_column}")
        selected = tuple(symbols or (column for column in returns.columns if column != timestamp_column))
        if not selected or len(set(selected)) != len(selected):
            raise ValueError("tail-risk symbols must be non-empty and unique")
        if missing := set(selected) - set(returns.columns):
            raise ValueError(f"return columns are missing: {', '.join(sorted(missing))}")
        timestamps = returns[timestamp_column].cast(pl.Datetime("ns"))
        maximum_timestamp = timestamps.max()
        if maximum_timestamp is None and as_of is None:
            raise ValueError("returns must contain at least one timestamp")
        cutoff = _datetime_ns(maximum_timestamp if as_of is None else as_of)
        if np.isnat(cutoff):
            raise ValueError("tail-risk cutoff cannot be NaT")
        candidates = (
            returns.with_columns(timestamps.alias(timestamp_column))
            .filter(pl.col(timestamp_column) <= cutoff)
            .sort(timestamp_column)
            .select(timestamp_column, *selected)
        )
        if candidates[timestamp_column].n_unique() != candidates.height:
            raise ValueError("tail-risk return timestamps must be unique")
        sample = (
            candidates
            .drop_nulls()
            .filter(pl.all_horizontal(*(pl.col(symbol).cast(pl.Float64).is_finite() for symbol in selected)))
            .tail(self.lookback)
        )
        values = sample.select(*selected).to_numpy().astype(float, copy=False)
        if values.shape[0] < self.min_observations:
            raise ValueError(
                f"tail-risk model requires {self.min_observations} complete observations; got {values.shape[0]}"
            )
        sample_as_of = sample[timestamp_column].max()
        if sample_as_of is None:
            raise ValueError("tail-risk sample contains no timestamps")
        return FittedTailRiskModel(
            selected,
            values,
            _datetime_ns(sample_as_of),
            self.confidence,
            self.horizon_days,
            self.method,
        )


@dataclass(frozen=True)
class PortfolioVaRMeasure:
    model: FittedTailRiskModel
    name: str = "value_at_risk"

    @property
    def market_data_as_of(self) -> np.datetime64:
        return self.model.as_of

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        estimate = self.model.evaluate(exposures)
        portfolio = exposures.head(1).with_columns(
            pl.lit("__portfolio__").alias("symbol"),
            pl.lit("__portfolio__").alias("contract_group"),
            pl.lit("portfolio").alias("asset_class"),
            pl.lit(estimate.currency).alias("currency"),
        )
        return _measure_rows(portfolio, self.name, pl.lit(estimate.value_at_risk), estimate.currency)


@dataclass(frozen=True)
class PortfolioExpectedShortfallMeasure:
    model: FittedTailRiskModel
    name: str = "expected_shortfall"

    @property
    def market_data_as_of(self) -> np.datetime64:
        return self.model.as_of

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        estimate = self.model.evaluate(exposures)
        portfolio = exposures.head(1).with_columns(
            pl.lit("__portfolio__").alias("symbol"),
            pl.lit("__portfolio__").alias("contract_group"),
            pl.lit("portfolio").alias("asset_class"),
            pl.lit(estimate.currency).alias("currency"),
        )
        return _measure_rows(portfolio, self.name, pl.lit(estimate.expected_shortfall), estimate.currency)
