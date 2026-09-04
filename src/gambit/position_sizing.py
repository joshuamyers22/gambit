"""Auditable volatility-targeted portfolio exposure sizing."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl

from gambit.calculation import CalculationContext
from gambit.covariance_risk import CovarianceEstimate, PortfolioRiskOverlayResult
from gambit.var_risk import FittedTailRiskModel


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
        if np.any(direction != 0) and direction_volatility == 0:
            raise ValueError("nonzero forecasts have zero modeled volatility and cannot be sized")
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


@dataclass(frozen=True)
class VaRTargetSizingResult:
    """Sized exposures and VaR diagnostics expressed as fractions of capital."""

    positions: pl.DataFrame
    capital: float
    target_var: float
    pre_overlay_var: float
    achieved_var: float
    overlay_multiplier: float
    tail_model_as_of: np.datetime64
    overlay_diagnostics: pl.DataFrame | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", self.positions.clone())
        if self.overlay_diagnostics is not None:
            object.__setattr__(self, "overlay_diagnostics", self.overlay_diagnostics.clone())


@dataclass(frozen=True)
class VaRTargetSizer:
    """Scale relative forecasts to a portfolio value-at-risk target."""

    target_var: float
    forecast_column: str = "raw_forecast"

    def __post_init__(self) -> None:
        if not math.isfinite(self.target_var) or self.target_var <= 0:
            raise ValueError("target VaR must be finite and positive")
        if not self.forecast_column:
            raise ValueError("forecast column cannot be empty")

    def size(
        self,
        forecasts: pl.DataFrame,
        model: FittedTailRiskModel,
        context: CalculationContext | np.datetime64,
        *,
        capital: float,
        overlay: PortfolioRiskOverlayResult | None = None,
    ) -> VaRTargetSizingResult:
        """Return new base-currency exposures without modifying forecasts."""
        calculation = CalculationContext.coerce(context)
        if not calculation.allow_lookahead and model.as_of > calculation.market_data_as_of:
            raise ValueError("tail-risk model uses market data after the calculation cutoff")
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
        unknown = set(forecast_by_symbol) - set(model.symbols)
        if unknown:
            raise ValueError(f"tail-risk model is missing symbols: {', '.join(sorted(unknown))}")
        direction_by_symbol = {
            symbol: float(forecast_by_symbol.get(symbol, 0.0)) for symbol in model.symbols
        }
        direction = pl.DataFrame(
            {
                "symbol": list(model.symbols),
                "net_exposure": list(direction_by_symbol.values()),
                "currency": [calculation.base_currency] * len(model.symbols),
            }
        )
        direction_var = model.evaluate(direction).value_at_risk
        if np.any(values != 0) and direction_var == 0:
            raise ValueError("nonzero forecasts have zero modeled value at risk and cannot be sized")
        scale = capital * self.target_var / direction_var if direction_var > 0 else 0.0

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
        pre_overlay_var = self._value_at_risk(positions, model, "target_net_exposure")
        achieved_var = self._value_at_risk(positions, model, "net_exposure")
        return VaRTargetSizingResult(
            positions,
            float(capital),
            self.target_var,
            pre_overlay_var / capital,
            achieved_var / capital,
            overlay_multiplier,
            model.as_of,
            None if overlay is None else overlay.diagnostics,
        )

    @staticmethod
    def _value_at_risk(
        positions: pl.DataFrame, model: FittedTailRiskModel, exposure_column: str
    ) -> float:
        exposures = positions.select(
            "symbol", "currency", pl.col(exposure_column).alias("net_exposure")
        )
        return model.evaluate(exposures).value_at_risk
