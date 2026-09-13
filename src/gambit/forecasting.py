"""Auditable fixed forecast scaling, capping, and combination."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

import numpy as np
import polars as pl
from numpy.typing import NDArray


class MissingForecastPolicy(str, Enum):
    """Treatment of an unavailable configured rule in fixed combination."""

    REQUIRE_ALL = "require_all"
    ZERO = "zero"
    RENORMALIZE = "renormalize"


def _positive_mapping(values: Mapping[str, float], *, name: str, allow_zero: bool = False) -> Mapping[str, float]:
    if not isinstance(values, Mapping) or not values:
        raise ValueError(f"forecast {name} must be a non-empty mapping")
    normalized: dict[str, float] = {}
    for rule, value in values.items():
        if not isinstance(rule, str) or not rule:
            raise ValueError(f"forecast {name} rule names must be non-empty strings")
        if type(value) is bool or not isinstance(value, (int, float, np.integer, np.floating)):
            raise TypeError(f"forecast {name} for {rule!r} must be numeric")
        number = float(value)
        if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
            qualifier = "non-negative" if allow_zero else "positive"
            raise ValueError(f"forecast {name} for {rule!r} must be finite and {qualifier}")
        normalized[rule] = number
    return MappingProxyType(dict(sorted(normalized.items())))


def _normalized_input(frame: pl.DataFrame) -> pl.DataFrame:
    if not isinstance(frame, pl.DataFrame):
        raise TypeError("forecast input must be a Polars DataFrame")
    required = {"timestamp", "symbol", "rule", "raw_forecast"}
    if missing := required - set(frame.columns):
        raise ValueError(f"forecast columns are missing: {', '.join(sorted(missing))}")
    if frame.is_empty():
        raise ValueError("forecast input cannot be empty")
    timestamp_dtype = frame.schema["timestamp"]
    if timestamp_dtype != pl.Date and not isinstance(timestamp_dtype, pl.Datetime):
        raise TypeError("forecast timestamp must be Polars Date or Datetime")
    if isinstance(timestamp_dtype, pl.Datetime) and timestamp_dtype.time_zone is not None:
        raise ValueError("forecast timestamps must be timezone-naive")
    normalized = frame.select(
        pl.col("timestamp").cast(pl.Datetime("ns")),
        pl.col("symbol").cast(pl.String),
        pl.col("rule").cast(pl.String),
        pl.col("raw_forecast").cast(pl.Float64),
    )
    if normalized.select(pl.any_horizontal(pl.col("timestamp", "symbol", "rule").is_null()).any()).item():
        raise ValueError("forecast timestamp, symbol, and rule cannot be null")
    if normalized.select(
        ((pl.col("symbol").str.len_chars() == 0) | (pl.col("rule").str.len_chars() == 0)).any()
    ).item():
        raise ValueError("forecast symbol and rule cannot be empty")
    if normalized.select(pl.struct(["timestamp", "symbol", "rule"]).is_duplicated().any()).item():
        raise ValueError("forecast input requires one row per timestamp, symbol, and rule")
    if normalized.select(pl.col("raw_forecast").is_not_null().and_(~pl.col("raw_forecast").is_finite()).any()).item():
        raise ValueError("available raw forecasts must be finite")
    return normalized.sort("timestamp", "symbol", "rule")


@dataclass(frozen=True)
class ForecastScaleCap:
    """Apply fixed positive rule scalars and a symmetric absolute cap."""

    scalars: Mapping[str, float]
    cap: float = 20.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "scalars", _positive_mapping(self.scalars, name="scalars"))
        if type(self.cap) is bool or not isinstance(self.cap, (int, float, np.integer, np.floating)):
            raise TypeError("forecast cap must be numeric")
        cap = float(self.cap)
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError("forecast cap must be finite and positive")
        object.__setattr__(self, "cap", cap)

    def transform(self, frame: pl.DataFrame) -> pl.DataFrame:
        """Return detached raw, scaled, capped, and availability observations."""
        normalized = _normalized_input(frame)
        unknown = sorted(set(normalized["rule"].unique()) - set(self.scalars))
        if unknown:
            raise ValueError(f"forecast scalars are missing rules: {', '.join(unknown)}")
        scalar_frame = pl.DataFrame({"rule": list(self.scalars), "scalar": list(self.scalars.values())})
        groups = normalized.select("timestamp", "symbol").unique(maintain_order=True)
        transformed = (
            groups.join(scalar_frame, how="cross")
            .join(normalized, on=["timestamp", "symbol", "rule"], how="left")
            .with_columns(
                pl.col("raw_forecast").is_not_null().alias("available"),
                (pl.col("raw_forecast") * pl.col("scalar")).alias("scaled_forecast"),
            )
            .with_columns(pl.col("scaled_forecast").clip(-self.cap, self.cap).alias("capped_forecast"))
        )
        if transformed.select(
            pl.col("scaled_forecast").is_not_null().and_(~pl.col("scaled_forecast").is_finite()).any()
        ).item():
            raise ValueError("scaled forecasts must be finite")
        return transformed.select(
            "timestamp",
            "symbol",
            "rule",
            "raw_forecast",
            "available",
            "scalar",
            "scaled_forecast",
            "capped_forecast",
        ).clone()


@dataclass(frozen=True)
class FittedForecastScalars:
    """Detached scalar estimates fitted through one declared timestamp."""

    scalars: Mapping[str, float]
    observations: Mapping[str, int]
    as_of: np.datetime64

    def __post_init__(self) -> None:
        scalars = _positive_mapping(self.scalars, name="fitted scalars")
        observations = dict(self.observations)
        if set(observations) != set(scalars):
            raise ValueError("forecast scalar observation rules must match fitted scalars")
        if any(type(value) is not int or value <= 0 for value in observations.values()):
            raise ValueError("forecast scalar observations must be positive integers")
        as_of: np.datetime64 = self.as_of.astype("datetime64[ns]")
        if np.isnat(as_of):
            raise ValueError("forecast scalar as_of cannot be NaT")
        object.__setattr__(self, "scalars", scalars)
        object.__setattr__(self, "observations", MappingProxyType(dict(sorted(observations.items()))))
        object.__setattr__(self, "as_of", as_of)

    def scale_cap(self, *, cap: float = 20.0) -> ForecastScaleCap:
        """Create the row-level transform using these fitted scalars."""
        return ForecastScaleCap(self.scalars, cap=cap)


@dataclass(frozen=True)
class ForecastScalarEstimator:
    """Fit rule scalars by targeting a historical mean absolute forecast."""

    target_mean_absolute_forecast: float = 10.0
    min_observations: int = 20

    def __post_init__(self) -> None:
        target = self.target_mean_absolute_forecast
        if (
            type(target) is bool
            or not isinstance(target, (int, float, np.integer, np.floating))
            or not math.isfinite(target)
            or target <= 0
        ):
            raise ValueError("target mean absolute forecast must be finite and positive")
        if type(self.min_observations) is not int or self.min_observations <= 0:
            raise ValueError("forecast scalar min_observations must be a positive integer")
        object.__setattr__(self, "target_mean_absolute_forecast", float(target))

    def fit(
        self,
        frame: pl.DataFrame,
        *,
        timestamp_column: str,
        rule_columns: Sequence[str],
        as_of: np.datetime64 | None = None,
    ) -> FittedForecastScalars:
        """Fit from wide rule columns at or before ``as_of``."""
        if not isinstance(frame, pl.DataFrame):
            raise TypeError("forecast scalar input must be a Polars DataFrame")
        if not isinstance(timestamp_column, str) or not timestamp_column:
            raise ValueError("forecast scalar timestamp_column must be a non-empty string")
        if isinstance(rule_columns, (str, bytes)) or not isinstance(rule_columns, Sequence) or not rule_columns:
            raise ValueError("forecast scalar rule_columns must be a non-empty sequence")
        rules = tuple(rule_columns)
        if any(not isinstance(rule, str) or not rule for rule in rules) or len(set(rules)) != len(rules):
            raise ValueError("forecast scalar rule_columns must contain unique non-empty strings")
        missing = {timestamp_column, *rules} - set(frame.columns)
        if missing:
            raise ValueError(f"forecast scalar columns are missing: {', '.join(sorted(missing))}")
        dtype = frame.schema[timestamp_column]
        if dtype != pl.Date and not isinstance(dtype, pl.Datetime):
            raise TypeError("forecast scalar timestamp must be Polars Date or Datetime")
        if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
            raise ValueError("forecast scalar timestamps must be timezone-naive")
        normalized = frame.select(
            pl.col(timestamp_column).cast(pl.Datetime("ns")),
            *(pl.col(rule).cast(pl.Float64) for rule in rules),
        )
        timestamps = normalized[timestamp_column].to_numpy()
        if normalized[timestamp_column].null_count() or (
            len(timestamps) > 1 and not bool(np.all(np.diff(timestamps.astype("datetime64[ns]").astype(np.int64)) > 0))
        ):
            raise ValueError("forecast scalar timestamps must be non-null, strictly increasing, and unique")
        cutoff = timestamps[-1].astype("datetime64[ns]") if as_of is None else as_of.astype("datetime64[ns]")
        if np.isnat(cutoff):
            raise ValueError("forecast scalar as_of cannot be NaT")
        permitted = normalized.filter(pl.col(timestamp_column) <= cutoff)
        if permitted.is_empty():
            raise ValueError("forecast scalar input has no observations at or before as_of")
        scalars: dict[str, float] = {}
        observations: dict[str, int] = {}
        for rule in rules:
            values = permitted[rule].drop_nulls().to_numpy()
            if not np.isfinite(values).all():
                raise ValueError(f"forecast scalar rule {rule!r} must contain only finite available values")
            if len(values) < self.min_observations:
                raise ValueError(
                    f"forecast scalar rule {rule!r} has {len(values)} observations; requires {self.min_observations}"
                )
            mean_absolute = float(np.mean(np.abs(values)))
            if mean_absolute == 0:
                raise ValueError(f"forecast scalar rule {rule!r} has zero mean absolute forecast")
            scalar = self.target_mean_absolute_forecast / mean_absolute
            if not math.isfinite(scalar):
                raise ValueError(f"forecast scalar rule {rule!r} produced a non-finite scalar")
            scalars[rule] = scalar
            observations[rule] = len(values)
        return FittedForecastScalars(scalars, observations, cutoff)


@dataclass(frozen=True, init=False)
class ForecastCombinationResult:
    """Detached combined forecasts and their complete rule contributions."""

    _forecasts: pl.DataFrame
    _contributions: pl.DataFrame

    def __init__(self, forecasts: pl.DataFrame, contributions: pl.DataFrame) -> None:
        object.__setattr__(self, "_forecasts", forecasts.clone())
        object.__setattr__(self, "_contributions", contributions.clone())

    @property
    def forecasts(self) -> pl.DataFrame:
        return self._forecasts.clone()

    @property
    def contributions(self) -> pl.DataFrame:
        return self._contributions.clone()


@dataclass(frozen=True)
class FixedForecastCombiner:
    """Combine capped rules with fixed normalized weights."""

    weights: Mapping[str, float]
    missing: MissingForecastPolicy = MissingForecastPolicy.REQUIRE_ALL
    diversification_multiplier: float = 1.0

    def __post_init__(self) -> None:
        weights = _positive_mapping(self.weights, name="weights", allow_zero=True)
        if not math.isclose(sum(weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("forecast weights must sum to one")
        if not isinstance(self.missing, MissingForecastPolicy):
            raise TypeError("missing forecast policy must be MissingForecastPolicy")
        multiplier = self.diversification_multiplier
        if (
            type(multiplier) is bool
            or not isinstance(multiplier, (int, float, np.integer, np.floating))
            or not math.isfinite(multiplier)
            or multiplier < 1
        ):
            raise ValueError("forecast diversification multiplier must be finite and at least one")
        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "diversification_multiplier", float(multiplier))

    @classmethod
    def equal(
        cls,
        rules: Sequence[str],
        *,
        missing: MissingForecastPolicy = MissingForecastPolicy.REQUIRE_ALL,
    ) -> FixedForecastCombiner:
        """Construct deterministic equal weights for unique rule names."""
        if isinstance(rules, (str, bytes)) or not isinstance(rules, Sequence) or not rules:
            raise ValueError("equal forecast rules must be a non-empty sequence")
        normalized = tuple(rules)
        if any(not isinstance(rule, str) or not rule for rule in normalized):
            raise ValueError("equal forecast rule names must be non-empty strings")
        if len(set(normalized)) != len(normalized):
            raise ValueError("equal forecast rules must be unique")
        weight = 1.0 / len(normalized)
        return cls(dict.fromkeys(normalized, weight), missing=missing)

    def combine(self, scaled: pl.DataFrame) -> ForecastCombinationResult:
        """Return one ``raw_forecast`` per timestamp/symbol plus contribution audit rows."""
        if not isinstance(scaled, pl.DataFrame):
            raise TypeError("scaled forecasts must be a Polars DataFrame")
        required = {
            "timestamp",
            "symbol",
            "rule",
            "raw_forecast",
            "available",
            "scalar",
            "scaled_forecast",
            "capped_forecast",
        }
        if missing := required - set(scaled.columns):
            raise ValueError(f"scaled forecast columns are missing: {', '.join(sorted(missing))}")
        if (
            scaled.schema["timestamp"] != pl.Datetime("ns")
            or scaled.schema["symbol"] != pl.String
            or scaled.schema["rule"] != pl.String
            or scaled.schema["available"] != pl.Boolean
            or any(
                not scaled.schema[column].is_numeric()
                for column in ("raw_forecast", "scalar", "scaled_forecast", "capped_forecast")
            )
        ):
            raise TypeError("scaled forecast schema does not match ForecastScaleCap output")
        if scaled.select(pl.col("timestamp", "symbol", "rule", "scalar").is_null().any()).row(0).count(True):
            raise ValueError("scaled forecast identities and scalars cannot be null")
        if scaled.select((pl.col("available") != pl.col("raw_forecast").is_not_null()).any()).item():
            raise ValueError("scaled forecast availability must match raw forecast presence")
        if any(
            scaled.select((pl.col(column).is_null() != ~pl.col("available")).any()).item()
            for column in ("scaled_forecast", "capped_forecast")
        ):
            raise ValueError("scaled and capped forecast presence must match availability")
        if scaled.select(
            (~pl.col("scalar").is_finite()).any()
            | pl.any_horizontal(
                pl.col("raw_forecast", "scaled_forecast", "capped_forecast").is_not_null()
                & ~pl.col("raw_forecast", "scaled_forecast", "capped_forecast").is_finite()
            ).any()
        ).item():
            raise ValueError("scaled forecast values must be finite when available")
        unknown = sorted(set(scaled["rule"].unique()) - set(self.weights))
        if unknown:
            raise ValueError(f"forecast weights are missing rules: {', '.join(unknown)}")
        if scaled.select(pl.struct(["timestamp", "symbol", "rule"]).is_duplicated().any()).item():
            raise ValueError("scaled forecasts require one row per timestamp, symbol, and rule")

        groups = scaled.select("timestamp", "symbol").unique(maintain_order=True)
        weight_frame = pl.DataFrame({"rule": list(self.weights), "weight": list(self.weights.values())})
        skeleton = groups.join(weight_frame, how="cross")
        values = scaled.select(
            "timestamp",
            "symbol",
            "rule",
            "raw_forecast",
            "available",
            "scalar",
            "scaled_forecast",
            "capped_forecast",
        )
        contributions = skeleton.join(values, on=["timestamp", "symbol", "rule"], how="left").with_columns(
            pl.col("available").fill_null(False),
        )
        if (
            self.missing is MissingForecastPolicy.REQUIRE_ALL
            and contributions.select((~pl.col("available")).any()).item()
        ):
            raise ValueError("fixed forecast combination requires every configured rule to be available")
        if self.missing is MissingForecastPolicy.RENORMALIZE:
            contributions = contributions.with_columns(
                pl.col("weight").filter(pl.col("available")).sum().over("timestamp", "symbol").alias("available_weight")
            )
            if contributions.select((pl.col("available_weight") <= 0).any()).item():
                raise ValueError("forecast renormalization requires at least one positive-weight available rule")
            effective_weight = (
                pl.when(pl.col("available")).then(pl.col("weight") / pl.col("available_weight")).otherwise(0.0)
            )
        else:
            effective_weight = pl.when(pl.col("available")).then(pl.col("weight")).otherwise(0.0)
        contributions = (
            contributions.with_columns(
                effective_weight.alias("effective_weight"),
                pl.lit(self.diversification_multiplier).alias("diversification_multiplier"),
            )
            .with_columns(
                pl.when(pl.col("available"))
                .then(pl.col("capped_forecast") * pl.col("effective_weight") * pl.col("diversification_multiplier"))
                .otherwise(0.0)
                .alias("contribution"),
            )
            .drop("available_weight", strict=False)
            .sort("timestamp", "symbol", "rule")
        )
        combined = (
            contributions.group_by("timestamp", "symbol", maintain_order=True)
            .agg(
                pl.col("contribution").sum().alias("raw_forecast"),
                pl.col("available").sum().cast(pl.UInt32).alias("available_rule_count"),
            )
            .sort("timestamp", "symbol")
        )
        if not bool(combined["raw_forecast"].is_finite().all()):
            raise ValueError("combined forecasts must be finite")
        return ForecastCombinationResult(combined, contributions)


@dataclass(frozen=True, init=False)
class FittedForecastCombination:
    """Detached training-only rule weights, correlation, and bounded scaling."""

    weights: Mapping[str, float]
    observations: int
    as_of: np.datetime64
    diversification_multiplier: float
    max_diversification_multiplier: float
    _correlation: NDArray[np.float64]

    def __init__(
        self,
        weights: Mapping[str, float],
        correlation: NDArray[np.float64],
        observations: int,
        as_of: np.datetime64,
        diversification_multiplier: float,
        max_diversification_multiplier: float,
    ) -> None:
        normalized_weights = _positive_mapping(weights, name="fitted weights")
        if not math.isclose(sum(normalized_weights.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("fitted forecast weights must sum to one")
        matrix = np.asarray(correlation, dtype=np.float64)
        size = len(normalized_weights)
        if matrix.shape != (size, size):
            raise ValueError("forecast correlation shape must match fitted weights")
        if not np.isfinite(matrix).all() or not np.allclose(matrix, matrix.T, rtol=0.0, atol=1e-12):
            raise ValueError("forecast correlation must be finite and symmetric")
        if not np.allclose(np.diag(matrix), 1.0, rtol=0.0, atol=1e-12):
            raise ValueError("forecast correlation diagonal must equal one")
        if np.any(matrix < -1.0) or np.any(matrix > 1.0):
            raise ValueError("forecast correlations must be in [-1, 1]")
        if float(np.linalg.eigvalsh(matrix).min()) < -1e-10:
            raise ValueError("forecast correlation must be positive semidefinite")
        if type(observations) is not int or observations <= 1:
            raise ValueError("forecast combination observations must be an integer greater than one")
        cutoff: np.datetime64 = as_of.astype("datetime64[ns]")
        if np.isnat(cutoff):
            raise ValueError("forecast combination as_of cannot be NaT")
        for name, value in (
            ("diversification multiplier", diversification_multiplier),
            ("maximum diversification multiplier", max_diversification_multiplier),
        ):
            if (
                type(value) is bool
                or not isinstance(value, (int, float, np.integer, np.floating))
                or not math.isfinite(value)
                or value < 1
            ):
                raise ValueError(f"forecast {name} must be finite and at least one")
        if diversification_multiplier > max_diversification_multiplier:
            raise ValueError("forecast diversification multiplier exceeds its configured maximum")
        weight_array = np.fromiter(normalized_weights.values(), dtype=np.float64)
        weighted_variance = float(weight_array @ matrix @ weight_array)
        if not math.isfinite(weighted_variance) or weighted_variance <= 0:
            raise ValueError("forecast correlation produces non-positive weighted variance")
        expected_multiplier = min(
            float(max_diversification_multiplier),
            1.0 / math.sqrt(weighted_variance),
        )
        if not math.isclose(float(diversification_multiplier), expected_multiplier, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("forecast diversification multiplier is inconsistent with weights and correlation")
        detached = matrix.copy()
        detached.setflags(write=False)
        object.__setattr__(self, "weights", normalized_weights)
        object.__setattr__(self, "_correlation", detached)
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "as_of", cutoff)
        object.__setattr__(self, "diversification_multiplier", float(diversification_multiplier))
        object.__setattr__(self, "max_diversification_multiplier", float(max_diversification_multiplier))

    @property
    def correlation(self) -> NDArray[np.float64]:
        """Return a detached rule correlation matrix in sorted weight order."""
        return self._correlation.copy()

    @property
    def rules(self) -> tuple[str, ...]:
        return tuple(self.weights)

    def combiner(
        self,
        *,
        missing: MissingForecastPolicy = MissingForecastPolicy.REQUIRE_ALL,
    ) -> FixedForecastCombiner:
        """Create a combiner using these fixed training-only estimates."""
        return FixedForecastCombiner(
            self.weights,
            missing=missing,
            diversification_multiplier=self.diversification_multiplier,
        )


@dataclass(frozen=True)
class ForecastCombinationEstimator:
    """Fit inverse-volatility weights and bounded correlation diversification."""

    min_observations: int = 60
    max_diversification_multiplier: float = 2.5

    def __post_init__(self) -> None:
        if type(self.min_observations) is not int or self.min_observations <= 1:
            raise ValueError("forecast combination min_observations must be an integer greater than one")
        maximum = self.max_diversification_multiplier
        if (
            type(maximum) is bool
            or not isinstance(maximum, (int, float, np.integer, np.floating))
            or not math.isfinite(maximum)
            or maximum < 1
        ):
            raise ValueError("maximum forecast diversification multiplier must be finite and at least one")
        object.__setattr__(self, "max_diversification_multiplier", float(maximum))

    def fit(
        self,
        frame: pl.DataFrame,
        *,
        timestamp_column: str,
        rule_columns: Sequence[str],
        as_of: np.datetime64 | None = None,
    ) -> FittedForecastCombination:
        """Fit from complete wide rule observations at or before ``as_of``."""
        if not isinstance(frame, pl.DataFrame):
            raise TypeError("forecast combination input must be a Polars DataFrame")
        if not isinstance(timestamp_column, str) or not timestamp_column:
            raise ValueError("forecast combination timestamp_column must be a non-empty string")
        if isinstance(rule_columns, (str, bytes)) or not isinstance(rule_columns, Sequence) or not rule_columns:
            raise ValueError("forecast combination rule_columns must be a non-empty sequence")
        requested_rules = tuple(rule_columns)
        if any(not isinstance(rule, str) or not rule for rule in requested_rules) or len(set(requested_rules)) != len(
            requested_rules
        ):
            raise ValueError("forecast combination rule_columns must contain unique non-empty strings")
        rules = tuple(sorted(requested_rules))
        missing = {timestamp_column, *rules} - set(frame.columns)
        if missing:
            raise ValueError(f"forecast combination columns are missing: {', '.join(sorted(missing))}")
        dtype = frame.schema[timestamp_column]
        if dtype != pl.Date and not isinstance(dtype, pl.Datetime):
            raise TypeError("forecast combination timestamp must be Polars Date or Datetime")
        if isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
            raise ValueError("forecast combination timestamps must be timezone-naive")
        normalized = frame.select(
            pl.col(timestamp_column).cast(pl.Datetime("ns")),
            *(pl.col(rule).cast(pl.Float64) for rule in rules),
        )
        timestamps = normalized[timestamp_column].to_numpy()
        if normalized[timestamp_column].null_count() or (
            len(timestamps) > 1 and not bool(np.all(np.diff(timestamps.astype("datetime64[ns]").astype(np.int64)) > 0))
        ):
            raise ValueError("forecast combination timestamps must be non-null, strictly increasing, and unique")
        cutoff = timestamps[-1].astype("datetime64[ns]") if as_of is None else as_of.astype("datetime64[ns]")
        if np.isnat(cutoff):
            raise ValueError("forecast combination as_of cannot be NaT")
        permitted = normalized.filter(pl.col(timestamp_column) <= cutoff)
        if permitted.is_empty():
            raise ValueError("forecast combination input has no observations at or before as_of")
        for rule in rules:
            available = permitted[rule].drop_nulls().to_numpy()
            if not np.isfinite(available).all():
                raise ValueError(f"forecast combination rule {rule!r} must contain only finite available values")
        complete = permitted.drop_nulls(list(rules))
        if complete.height < self.min_observations:
            raise ValueError(
                f"forecast combination has {complete.height} complete observations; requires {self.min_observations}"
            )
        values = complete.select(rules).to_numpy().astype(np.float64, copy=False)
        volatility = np.std(values, axis=0, ddof=1)
        zero_variance = [rule for rule, value in zip(rules, volatility, strict=True) if value <= 0]
        if zero_variance:
            raise ValueError(f"forecast combination rules have zero variance: {', '.join(zero_variance)}")
        inverse = 1.0 / volatility
        weights_array = inverse / inverse.sum()
        if len(rules) == 1:
            correlation: NDArray[np.float64] = np.ones((1, 1), dtype=np.float64)
        else:
            correlation = np.corrcoef(values, rowvar=False)
            correlation = np.clip((correlation + correlation.T) / 2.0, -1.0, 1.0)
            np.fill_diagonal(correlation, 1.0)
        variance = float(weights_array @ correlation @ weights_array)
        if not math.isfinite(variance) or variance <= 0:
            raise ValueError("forecast combination correlation produces non-positive weighted variance")
        unconstrained = 1.0 / math.sqrt(variance)
        multiplier = min(self.max_diversification_multiplier, unconstrained)
        weights = dict(zip(rules, weights_array.tolist(), strict=True))
        return FittedForecastCombination(
            weights,
            correlation,
            complete.height,
            cutoff,
            multiplier,
            self.max_diversification_multiplier,
        )
