from __future__ import annotations

from datetime import datetime

import numpy as np
import polars as pl
import pytest

from gambit.covariance_risk import CovarianceEstimate
from gambit.forecasting import (
    FixedForecastCombiner,
    ForecastScalarEstimator,
    ForecastScaleCap,
    MissingForecastPolicy,
)
from gambit.position_sizing import VolatilityTargetSizer

pytestmark = pytest.mark.acceptance


def _raw_forecasts() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": np.array(
                ["2026-01-01", "2026-01-01", "2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
                dtype="datetime64[ns]",
            ),
            "symbol": ["A", "A", "B", "B", "A", "A"],
            "rule": ["carry", "momentum", "carry", "momentum", "carry", "momentum"],
            "raw_forecast": [-1.0, 2.0, 1.0, 1.0, 3.0, -3.0],
        }
    )


def _scaled(frame: pl.DataFrame | None = None) -> pl.DataFrame:
    return ForecastScaleCap({"carry": 1.0, "momentum": 2.0}, cap=3.0).transform(
        _raw_forecasts() if frame is None else frame
    )


def test_fixed_scaling_capping_and_contributions_match_manual_values() -> None:
    scaled = _scaled()
    result = FixedForecastCombiner({"carry": 0.75, "momentum": 0.25}).combine(scaled)

    assert scaled["scaled_forecast"].to_list() == [-1.0, 4.0, 1.0, 2.0, 3.0, -6.0]
    assert scaled["capped_forecast"].to_list() == [-1.0, 3.0, 1.0, 2.0, 3.0, -3.0]
    assert result.forecasts.select("symbol", "raw_forecast").rows() == [
        ("A", 0.0),
        ("B", 1.25),
        ("A", 1.5),
    ]
    first = result.contributions.filter(
        (pl.col("timestamp") == np.datetime64("2026-01-01")) & (pl.col("symbol") == "A")
    )
    assert first.select("rule", "available", "weight", "contribution").rows() == [
        ("carry", True, 0.75, -0.75),
        ("momentum", True, 0.25, 0.75),
    ]


def test_equal_correlated_rules_do_not_receive_diversification_credit() -> None:
    raw = pl.DataFrame(
        {
            "timestamp": np.array(["2026-01-01", "2026-01-01"], dtype="datetime64[ns]"),
            "symbol": ["A", "A"],
            "rule": ["first", "duplicate"],
            "raw_forecast": [4.0, 4.0],
        }
    )
    scaled = ForecastScaleCap({"first": 1.0, "duplicate": 1.0}, cap=20.0).transform(raw)

    result = FixedForecastCombiner.equal(["first", "duplicate"]).combine(scaled)

    assert result.forecasts[0, "raw_forecast"] == 4.0
    assert result.contributions["weight"].to_list() == [0.5, 0.5]


def test_missing_rules_are_explicit_and_never_silently_renormalized() -> None:
    raw = _raw_forecasts().filter(
        ~((pl.col("timestamp") == np.datetime64("2026-01-02")) & (pl.col("rule") == "momentum"))
    )
    scaled = _scaled(raw)

    with pytest.raises(ValueError, match="requires every configured rule"):
        FixedForecastCombiner.equal(["carry", "momentum"]).combine(scaled)

    result = FixedForecastCombiner.equal(
        ["carry", "momentum"], missing=MissingForecastPolicy.ZERO
    ).combine(scaled)
    missing = result.contributions.filter(pl.col("timestamp") == np.datetime64("2026-01-02"))
    assert missing.select("rule", "available", "effective_weight", "contribution").rows() == [
        ("carry", True, 0.5, 1.5),
        ("momentum", False, 0.0, 0.0),
    ]
    assert missing.filter(pl.col("rule") == "momentum")[0, "scalar"] == 2.0
    assert result.forecasts.filter(pl.col("timestamp") == np.datetime64("2026-01-02")).row(0, named=True) == {
        "timestamp": datetime(2026, 1, 2),
        "symbol": "A",
        "raw_forecast": 1.5,
        "available_rule_count": 1,
    }


def test_fixed_pipeline_is_row_local_and_feeds_existing_volatility_sizer() -> None:
    scale = ForecastScaleCap({"carry": 1.0, "momentum": 2.0}, cap=3.0)
    combine = FixedForecastCombiner({"carry": 0.75, "momentum": 0.25})
    early_raw = _raw_forecasts().filter(pl.col("timestamp") == np.datetime64("2026-01-01"))

    early = combine.combine(scale.transform(early_raw)).forecasts
    full_early = combine.combine(scale.transform(_raw_forecasts())).forecasts.filter(
        pl.col("timestamp") == np.datetime64("2026-01-01")
    )

    assert early.equals(full_early)
    estimate = CovarianceEstimate(
        ("A", "B"),
        np.array([[0.04, 0.01], [0.01, 0.09]]),
        np.datetime64("2026-01-01", "ns"),
        100,
        252.0,
    )
    sized = VolatilityTargetSizer(0.10).size(
        early, estimate, np.datetime64("2026-01-01", "ns"), capital=1_000_000
    )

    assert sized.positions["raw_forecast"].to_list() == [0.0, 1.25]
    assert sized.achieved_volatility == pytest.approx(0.10)


def test_forecast_stages_return_detached_frames() -> None:
    scaled = _scaled()
    result = FixedForecastCombiner.equal(["carry", "momentum"]).combine(scaled)
    forecasts = result.forecasts
    contributions = result.contributions
    forecasts[0, "raw_forecast"] = 999.0
    contributions[0, "contribution"] = 999.0

    assert result.forecasts[0, "raw_forecast"] != 999.0
    assert result.contributions[0, "contribution"] != 999.0


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: ForecastScaleCap({"carry": 0.0}), "finite and positive"),
        (lambda: ForecastScaleCap({"carry": 1.0}, cap=float("inf")), "finite and positive"),
        (lambda: FixedForecastCombiner({"carry": 0.8}), "sum to one"),
        (lambda: FixedForecastCombiner.equal(["carry", "carry"]), "must be unique"),
    ],
)
def test_forecast_configuration_rejects_ambiguous_values(factory, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_forecast_input_rejects_duplicates_unknown_rules_and_nonfinite_values() -> None:
    scale = ForecastScaleCap({"carry": 1.0, "momentum": 1.0})
    with pytest.raises(ValueError, match="one row per timestamp"):
        scale.transform(pl.concat([_raw_forecasts(), _raw_forecasts().head(1)]))
    with pytest.raises(ValueError, match="scalars are missing rules"):
        ForecastScaleCap({"carry": 1.0}).transform(_raw_forecasts())
    with pytest.raises(ValueError, match="must be finite"):
        scale.transform(_raw_forecasts().with_columns(pl.lit(float("nan")).alias("raw_forecast")))


def test_scalar_estimator_uses_only_values_through_declared_cutoff() -> None:
    frame = pl.DataFrame(
        {
            "timestamp": np.arange(
                np.datetime64("2026-01-01"), np.datetime64("2026-01-06"), dtype="datetime64[D]"
            ).astype("datetime64[ns]"),
            "carry": [-2.0, -1.0, 1.0, 2.0, 100.0],
            "momentum": [1.0, 1.0, 1.0, 1.0, 100.0],
        }
    )
    cutoff = np.datetime64("2026-01-04", "ns")
    estimator = ForecastScalarEstimator(target_mean_absolute_forecast=10.0, min_observations=4)

    fitted = estimator.fit(
        frame,
        timestamp_column="timestamp",
        rule_columns=["carry", "momentum"],
        as_of=cutoff,
    )
    changed = estimator.fit(
        frame.with_columns(pl.when(pl.col("timestamp") > cutoff).then(999.0).otherwise(pl.col("carry")).alias("carry")),
        timestamp_column="timestamp",
        rule_columns=["carry", "momentum"],
        as_of=cutoff,
    )

    assert fitted == changed
    assert fitted.as_of == cutoff
    assert fitted.observations == {"carry": 4, "momentum": 4}
    assert fitted.scalars["carry"] == pytest.approx(10.0 / 1.5)
    assert fitted.scalars["momentum"] == 10.0


def test_fitted_scalars_feed_scale_cap_and_reject_insufficient_or_zero_history() -> None:
    estimator = ForecastScalarEstimator(min_observations=2)
    frame = pl.DataFrame(
        {
            "timestamp": np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[ns]"),
            "carry": [1.0, -1.0],
        }
    )
    fitted = estimator.fit(frame, timestamp_column="timestamp", rule_columns=["carry"])
    raw = pl.DataFrame(
        {
            "timestamp": np.array(["2026-01-03"], dtype="datetime64[ns]"),
            "symbol": ["A"],
            "rule": ["carry"],
            "raw_forecast": [3.0],
        }
    )

    assert fitted.scale_cap(cap=20.0).transform(raw)[0, "capped_forecast"] == 20.0
    with pytest.raises(ValueError, match="requires 3"):
        ForecastScalarEstimator(min_observations=3).fit(
            frame, timestamp_column="timestamp", rule_columns=["carry"]
        )
    with pytest.raises(ValueError, match="zero mean absolute"):
        estimator.fit(
            frame.with_columns(pl.lit(0.0).alias("carry")),
            timestamp_column="timestamp",
            rule_columns=["carry"],
        )
