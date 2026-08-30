"""Auditable volatility-targeted portfolio exposure sizing."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl

from gambit.calculation import CalculationContext
from gambit.covariance_risk import CovarianceEstimate, PortfolioRiskOverlayResult


@dataclass(frozen=True)
class VolatilityTargetSizingResult:
    """Sized base-currency exposures and their portfolio-level diagnostics."""

    positions: pl.DataFrame
    capital: float
    target_volatility: float
    pre_overlay_volatility: float
    achieved_volatility: float
    overlay_multiplier: float
    covariance_as_of: np.datetime64
    overlay_diagnostics: pl.DataFrame | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", self.positions.clone())
        if self.overlay_diagnostics is not None:
            object.__setattr__(self, "overlay_diagnostics", self.overlay_diagnostics.clone())


@dataclass(frozen=True)
class VolatilityTargetSizer:
    """Scale forecast direction to a portfolio volatility target.

    Forecast magnitude supplies relative position direction and conviction. The
    common scale is selected so estimated annualized cash volatility equals
    ``capital * target_volatility`` before an optional risk overlay is applied.
    """

    target_volatility: float
    forecast_column: str = "raw_forecast"

    def __post_init__(self) -> None:
        if not math.isfinite(self.target_volatility) or self.target_volatility <= 0:
            raise ValueError("target volatility must be finite and positive")
        if not self.forecast_column:
            raise ValueError("forecast column cannot be empty")

    def size(
        self,
        forecasts: pl.DataFrame,
        estimate: CovarianceEstimate,
        context: CalculationContext | np.datetime64,
        *,
        capital: float,
        overlay: PortfolioRiskOverlayResult | None = None,
    ) -> VolatilityTargetSizingResult:
        """Return new exposure rows without modifying the supplied forecasts."""
        calculation = CalculationContext.coerce(context)
        if not calculation.allow_lookahead and estimate.as_of > calculation.market_data_as_of:
            raise ValueError("covariance estimate uses market data after the calculation cutoff")
        if not math.isfinite(capital) or capital <= 0:
            raise ValueError("capital must be finite and positive")
        required = {"symbol", self.forecast_column}
        if missing := required - set(forecasts.columns):
            raise ValueError(f"forecast columns are missing: {', '.join(sorted(missing))}")
        if forecasts.is_empty():
            raise ValueError("forecasts cannot be empty")
        if forecasts["symbol"].null_count() or forecasts[self.forecast_column].null_count():
            raise ValueError("symbols and forecasts cannot be null")
        if forecasts["symbol"].n_unique() != forecasts.height:
            raise ValueError("sizing requires one forecast row per symbol")
        if "currency" in forecasts.columns:
            if forecasts["currency"].null_count():
                raise ValueError("sizing input currency cannot be null")
            currencies = forecasts["currency"].drop_nulls().unique().to_list()
            if len(currencies) != 1 or str(currencies[0]).upper() != calculation.base_currency:
                raise ValueError("sizing inputs must be translated to the calculation base currency")

        prepared = forecasts.with_columns(
            pl.col("symbol").cast(pl.String),
            pl.col(self.forecast_column).cast(pl.Float64),
        )
        values = prepared[self.forecast_column].to_numpy()
        if not np.isfinite(values).all():
            raise ValueError("forecasts must be finite")
        forecast_by_symbol = dict(prepared.select("symbol", self.forecast_column).iter_rows())
        unknown = set(forecast_by_symbol) - set(estimate.symbols)
        if unknown:
            raise ValueError(f"covariance estimate is missing symbols: {', '.join(sorted(unknown))}")

        direction = np.asarray([float(forecast_by_symbol.get(symbol, 0.0)) for symbol in estimate.symbols])
        direction_variance = float(direction @ estimate.matrix @ direction)
        direction_volatility = math.sqrt(max(direction_variance, 0.0))
        scale = capital * self.target_volatility / direction_volatility if direction_volatility > 0 else 0.0

        overlay_multiplier = 1.0 if overlay is None else float(overlay.multiplier)
        if not math.isfinite(overlay_multiplier) or not 0 <= overlay_multiplier <= 1:
            raise ValueError("overlay multiplier must be finite and in [0, 1]")
        pre_overlay = values * scale
        sized = pre_overlay * overlay_multiplier
        positions = prepared.with_columns(
            pl.lit(calculation.base_currency).alias("currency"),
            pl.Series("target_net_exposure", pre_overlay),
            pl.lit(overlay_multiplier).alias("overlay_multiplier"),
            pl.Series("net_exposure", sized),
            pl.Series("gross_exposure", np.abs(sized)),
        )
        pre_overlay_volatility = self._portfolio_volatility(positions, estimate, "target_net_exposure")
        achieved_volatility = self._portfolio_volatility(positions, estimate, "net_exposure")
        return VolatilityTargetSizingResult(
            positions=positions,
            capital=float(capital),
            target_volatility=self.target_volatility,
            pre_overlay_volatility=pre_overlay_volatility / capital,
            achieved_volatility=achieved_volatility / capital,
            overlay_multiplier=overlay_multiplier,
            covariance_as_of=estimate.as_of,
            overlay_diagnostics=None if overlay is None else overlay.diagnostics,
        )

    @staticmethod
    def _portfolio_volatility(
        positions: pl.DataFrame, estimate: CovarianceEstimate, exposure_column: str
    ) -> float:
        exposures = positions.select("symbol", pl.col(exposure_column).alias("net_exposure"))
        return estimate.portfolio_volatility(exposures)
