"""Point-in-time covariance estimation and portfolio risk controls."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import polars as pl
from numpy.typing import NDArray

from gambit.risk_measures import _measure_rows


def _readonly_matrix(value: NDArray[np.float64]) -> NDArray[np.float64]:
    matrix = np.asarray(value, dtype=float).copy()
    matrix.setflags(write=False)
    return matrix


def _datetime_ns(value: object) -> np.datetime64:
    return np.datetime64(str(value)).astype("datetime64[ns]")


@dataclass(frozen=True)
class CovarianceEstimate:
    """Annualized covariance matrix with its estimation metadata."""

    symbols: tuple[str, ...]
    matrix: NDArray[np.float64]
    as_of: np.datetime64
    observations: int
    annualization_factor: float

    def __post_init__(self) -> None:
        symbols = tuple(self.symbols)
        matrix = np.asarray(self.matrix, dtype=float)
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValueError("covariance symbols must be non-empty and unique")
        if matrix.shape != (len(symbols), len(symbols)):
            raise ValueError("covariance matrix shape must match symbols")
        if not np.isfinite(matrix).all():
            raise ValueError("covariance matrix must be finite")
        if not np.allclose(matrix, matrix.T, rtol=1e-10, atol=1e-12):
            raise ValueError("covariance matrix must be symmetric")
        if np.linalg.eigvalsh(matrix).min() < -1e-10:
            raise ValueError("covariance matrix must be positive semidefinite")
        if self.observations < 2:
            raise ValueError("covariance estimate requires at least two observations")
        if not math.isfinite(self.annualization_factor) or self.annualization_factor <= 0:
            raise ValueError("annualization factor must be finite and positive")
        as_of = _datetime_ns(self.as_of)
        if np.isnat(as_of):
            raise ValueError("covariance as_of cannot be NaT")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "matrix", _readonly_matrix(matrix))
        object.__setattr__(self, "as_of", as_of)

    @property
    def volatilities(self) -> NDArray[np.float64]:
        values = np.sqrt(np.maximum(np.diag(self.matrix), 0.0))
        values.setflags(write=False)
        return values

    @property
    def correlation(self) -> NDArray[np.float64]:
        volatilities = self.volatilities
        denominator = np.outer(volatilities, volatilities)
        correlation = np.divide(
            self.matrix,
            denominator,
            out=np.eye(len(self.symbols), dtype=float),
            where=denominator > 0,
        )
        np.fill_diagonal(correlation, 1.0)
        correlation.setflags(write=False)
        return correlation

    def with_volatility_stress(self, multiplier: float) -> CovarianceEstimate:
        """Scale every annualized volatility while retaining correlations."""
        if not math.isfinite(multiplier) or multiplier <= 0:
            raise ValueError("volatility multiplier must be finite and positive")
        return CovarianceEstimate(
            self.symbols,
            self.matrix * multiplier**2,
            self.as_of,
            self.observations,
            self.annualization_factor,
        )

    def with_correlation_stress(self, severity: float) -> CovarianceEstimate:
        """Move correlations toward positive one while retaining volatilities."""
        if not math.isfinite(severity) or not 0 <= severity <= 1:
            raise ValueError("correlation stress severity must be in [0, 1]")
        correlation = (1 - severity) * self.correlation + severity * np.ones_like(self.matrix)
        matrix = np.outer(self.volatilities, self.volatilities) * correlation
        return CovarianceEstimate(
            self.symbols,
            matrix,
            self.as_of,
            self.observations,
            self.annualization_factor,
        )

    def with_adverse_correlation_stress(
        self, exposures: pl.DataFrame, severity: float
    ) -> CovarianceEstimate:
        """Move correlations toward the rank-one matrix worst for current position signs."""
        if not math.isfinite(severity) or not 0 <= severity <= 1:
            raise ValueError("correlation stress severity must be in [0, 1]")
        signs = np.sign(self.exposure_vector(exposures))
        signs[signs == 0] = 1.0
        adverse_target = np.outer(signs, signs)
        correlation = (1 - severity) * self.correlation + severity * adverse_target
        matrix = np.outer(self.volatilities, self.volatilities) * correlation
        return CovarianceEstimate(
            self.symbols,
            matrix,
            self.as_of,
            self.observations,
            self.annualization_factor,
        )

    def exposure_vector(self, exposures: pl.DataFrame) -> NDArray[np.float64]:
        required = {"symbol", "net_exposure"}
        if missing := required - set(exposures.columns):
            raise ValueError(f"exposure columns are missing: {', '.join(sorted(missing))}")
        grouped = exposures.group_by("symbol").agg(pl.col("net_exposure").sum())
        values = dict(grouped.select("symbol", "net_exposure").iter_rows())
        unknown = set(values) - set(self.symbols)
        if unknown:
            raise ValueError(f"covariance estimate is missing symbols: {', '.join(sorted(unknown))}")
        return np.asarray([float(values.get(symbol, 0.0)) for symbol in self.symbols])

    def portfolio_volatility(self, exposures: pl.DataFrame) -> float:
        vector = self.exposure_vector(exposures)
        variance = float(vector @ self.matrix @ vector)
        return math.sqrt(max(variance, 0.0))


@dataclass(frozen=True)
class CovarianceRiskModel:
    """Estimate annualized covariance from a wide Polars return table."""

    lookback: int = 252
    min_observations: int = 60
    annualization_factor: float = 252.0
    diagonal_shrinkage: float = 0.0

    def __post_init__(self) -> None:
        if self.lookback < 2:
            raise ValueError("lookback must be at least two")
        if self.min_observations < 2 or self.min_observations > self.lookback:
            raise ValueError("min_observations must be between two and lookback")
        if not math.isfinite(self.annualization_factor) or self.annualization_factor <= 0:
            raise ValueError("annualization factor must be finite and positive")
        if not math.isfinite(self.diagonal_shrinkage) or not 0 <= self.diagonal_shrinkage <= 1:
            raise ValueError("diagonal shrinkage must be in [0, 1]")

    def fit(
        self,
        returns: pl.DataFrame,
        *,
        timestamp_column: str = "timestamp",
        symbols: Sequence[str] | None = None,
        as_of: np.datetime64 | None = None,
    ) -> CovarianceEstimate:
        if timestamp_column not in returns.columns:
            raise ValueError(f"timestamp column is missing: {timestamp_column}")
        selected_symbols = tuple(symbols or (column for column in returns.columns if column != timestamp_column))
        if not selected_symbols or len(set(selected_symbols)) != len(selected_symbols):
            raise ValueError("return symbols must be non-empty and unique")
        if missing := set(selected_symbols) - set(returns.columns):
            raise ValueError(f"return columns are missing: {', '.join(sorted(missing))}")

        timestamps = returns[timestamp_column].cast(pl.Datetime("ns"))
        maximum_timestamp = timestamps.max()
        if maximum_timestamp is None and as_of is None:
            raise ValueError("returns must contain at least one finite timestamp")
        effective_as_of = _datetime_ns(maximum_timestamp if as_of is None else as_of)
        if np.isnat(effective_as_of):
            raise ValueError("covariance as_of cannot be NaT")
        sample = (
            returns.with_columns(timestamps.alias(timestamp_column))
            .filter(pl.col(timestamp_column) <= effective_as_of)
            .sort(timestamp_column)
            .select(timestamp_column, *selected_symbols)
            .drop_nulls()
            .tail(self.lookback)
        )
        values = sample.select(*selected_symbols).to_numpy().astype(float, copy=False)
        values = values[np.isfinite(values).all(axis=1)]
        if values.shape[0] < self.min_observations:
            raise ValueError(
                f"covariance estimate requires {self.min_observations} complete observations; got {values.shape[0]}"
            )
        sample_as_of = sample[timestamp_column].max()
        if sample_as_of is None:
            raise ValueError("covariance sample contains no timestamps")

        matrix = np.atleast_2d(np.cov(values, rowvar=False, ddof=1)) * self.annualization_factor
        diagonal = np.diag(np.diag(matrix))
        matrix = (1 - self.diagonal_shrinkage) * matrix + self.diagonal_shrinkage * diagonal
        matrix = (matrix + matrix.T) / 2
        return CovarianceEstimate(
            selected_symbols,
            matrix,
            _datetime_ns(sample_as_of),
            values.shape[0],
            self.annualization_factor,
        )


def _require_single_currency(exposures: pl.DataFrame) -> str:
    if "currency" not in exposures.columns:
        raise ValueError("exposure columns are missing: currency")
    currencies = exposures["currency"].drop_nulls().unique().to_list()
    if len(currencies) != 1:
        raise ValueError("covariance risk requires exposures translated to one currency")
    return str(currencies[0])


@dataclass(frozen=True)
class PortfolioVolatilityMeasure:
    estimate: CovarianceEstimate
    name: str = "portfolio_volatility"

    @property
    def market_data_as_of(self) -> np.datetime64:
        return self.estimate.as_of

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        currency = _require_single_currency(exposures)
        value = self.estimate.portfolio_volatility(exposures)
        portfolio = pl.DataFrame(
            {
                "symbol": ["__portfolio__"],
                "contract_group": ["__portfolio__"],
                "asset_class": ["portfolio"],
                "currency": [currency],
                "value": [value],
            }
        )
        return portfolio.with_columns(
            pl.lit(self.name).alias("measure"),
            pl.lit(None, dtype=pl.String).alias("scenario"),
            pl.lit(currency).alias("unit"),
        ).select("symbol", "contract_group", "asset_class", "currency", "measure", "scenario", "unit", "value")


@dataclass(frozen=True)
class ComponentVolatilityMeasure:
    estimate: CovarianceEstimate
    name: str = "component_volatility"

    @property
    def market_data_as_of(self) -> np.datetime64:
        return self.estimate.as_of

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        _require_single_currency(exposures)
        if exposures["symbol"].n_unique() != exposures.height:
            raise ValueError("component volatility requires one exposure row per symbol")
        vector = self.estimate.exposure_vector(exposures)
        volatility = self.estimate.portfolio_volatility(exposures)
        if math.isclose(volatility, 0.0, abs_tol=1e-15):
            components = np.zeros_like(vector)
        else:
            components = vector * (self.estimate.matrix @ vector) / volatility
        component_by_symbol = dict(zip(self.estimate.symbols, components, strict=True))
        values = pl.col("symbol").replace_strict(component_by_symbol, default=0.0, return_dtype=pl.Float64)
        return _measure_rows(exposures, self.name, values, pl.col("currency"))


@dataclass(frozen=True)
class DiversificationRatioMeasure:
    estimate: CovarianceEstimate
    name: str = "diversification_ratio"

    @property
    def market_data_as_of(self) -> np.datetime64:
        return self.estimate.as_of

    def calculate(self, exposures: pl.DataFrame) -> pl.DataFrame:
        currency = _require_single_currency(exposures)
        vector = self.estimate.exposure_vector(exposures)
        volatility = self.estimate.portfolio_volatility(exposures)
        numerator = float(np.abs(vector) @ self.estimate.volatilities)
        ratio = numerator / volatility if volatility > 0 else 0.0
        portfolio = pl.DataFrame(
            {
                "symbol": ["__portfolio__"],
                "contract_group": ["__portfolio__"],
                "asset_class": ["portfolio"],
                "currency": [currency],
                "value": [ratio],
            }
        )
        return portfolio.with_columns(
            pl.lit(self.name).alias("measure"),
            pl.lit(None, dtype=pl.String).alias("scenario"),
            pl.lit("ratio").alias("unit"),
        ).select("symbol", "contract_group", "asset_class", "currency", "measure", "scenario", "unit", "value")


@dataclass(frozen=True)
class PortfolioRiskLimits:
    max_portfolio_volatility: float
    max_stressed_volatility: float
    max_sum_absolute_risk: float
    max_leverage: float

    def __post_init__(self) -> None:
        values = (
            self.max_portfolio_volatility,
            self.max_stressed_volatility,
            self.max_sum_absolute_risk,
            self.max_leverage,
        )
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("portfolio risk limits must be finite and positive")


@dataclass(frozen=True)
class PortfolioRiskOverlayResult:
    multiplier: float
    diagnostics: pl.DataFrame

    def __post_init__(self) -> None:
        if not math.isfinite(self.multiplier) or not 0 <= self.multiplier <= 1:
            raise ValueError("overlay multiplier must be finite and in [0, 1]")
        required = {"constraint", "value", "limit", "multiplier"}
        if missing := required - set(self.diagnostics.columns):
            raise ValueError(f"overlay diagnostic columns are missing: {', '.join(sorted(missing))}")
        if self.diagnostics.is_empty():
            raise ValueError("overlay diagnostics cannot be empty")
        diagnostic_values = self.diagnostics.select("value", "limit", "multiplier").to_numpy()
        if not np.isfinite(diagnostic_values).all():
            raise ValueError("overlay diagnostics must be finite")
        multipliers = self.diagnostics["multiplier"].cast(pl.Float64)
        if (multipliers < 0).any() or (multipliers > 1).any():
            raise ValueError("overlay diagnostic multipliers must be in [0, 1]")
        if not math.isclose(self.multiplier, float(multipliers.min()), rel_tol=1e-12, abs_tol=1e-15):
            raise ValueError("overlay multiplier must equal the minimum diagnostic multiplier")
        object.__setattr__(self, "diagnostics", self.diagnostics.clone())


@dataclass(frozen=True)
class PortfolioRiskOverlay:
    limits: PortfolioRiskLimits

    def evaluate(
        self,
        exposures: pl.DataFrame,
        estimate: CovarianceEstimate,
        *,
        capital: float,
        stressed_estimate: CovarianceEstimate | None = None,
    ) -> PortfolioRiskOverlayResult:
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("capital must be finite and positive")
        _require_single_currency(exposures)
        if "gross_exposure" not in exposures.columns:
            raise ValueError("exposure columns are missing: gross_exposure")
        stressed = stressed_estimate or estimate
        if stressed.symbols != estimate.symbols:
            raise ValueError("normal and stressed covariance symbols must match")
        if stressed.as_of != estimate.as_of:
            raise ValueError("normal and stressed covariance timestamps must match")

        vector = estimate.exposure_vector(exposures)
        measures = {
            "portfolio_volatility": estimate.portfolio_volatility(exposures) / capital,
            "stressed_volatility": stressed.portfolio_volatility(exposures) / capital,
            "sum_absolute_risk": float(np.abs(vector) @ estimate.volatilities) / capital,
            "leverage": float(exposures["gross_exposure"].sum()) / capital,
        }
        limits = {
            "portfolio_volatility": self.limits.max_portfolio_volatility,
            "stressed_volatility": self.limits.max_stressed_volatility,
            "sum_absolute_risk": self.limits.max_sum_absolute_risk,
            "leverage": self.limits.max_leverage,
        }
        rows = []
        for name, value in measures.items():
            limit = limits[name]
            multiplier = min(1.0, limit / value) if value > 0 else 1.0
            rows.append({"constraint": name, "value": value, "limit": limit, "multiplier": multiplier})
        diagnostics = pl.DataFrame(rows)
        return PortfolioRiskOverlayResult(float(diagnostics["multiplier"].min()), diagnostics)
