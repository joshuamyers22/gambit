from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import polars as pl
import pytest

from gambit.optimize import (
    WalkForwardConfig,
    WalkForwardFold,
    WalkForwardInterval,
    WalkForwardRunner,
    WalkForwardSchedule,
    WalkForwardWindow,
)

pytestmark = pytest.mark.acceptance


def _timestamps(size: int = 18) -> np.ndarray:
    return np.arange(
        np.datetime64("2024-01-01"),
        np.datetime64("2024-01-01") + np.timedelta64(size, "D"),
        dtype="datetime64[D]",
    )


def _config(window: WalkForwardWindow = WalkForwardWindow.ROLLING) -> WalkForwardConfig:
    return WalkForwardConfig(
        fit_size=4,
        validation_size=2,
        heldout_size=2,
        warmup_size=1,
        purge_size=1,
        refit_every=2,
        window=window,
    )


def _frame(size: int = 18) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": _timestamps(size).astype("datetime64[ns]"),
            "feature": np.arange(size, dtype=float),
            "target": np.arange(size, dtype=float) * 2.0,
        }
    )


def test_rolling_schedule_exposes_explicit_purged_intervals() -> None:
    folds = WalkForwardSchedule(_timestamps(), _config()).folds

    assert len(folds) == 4
    assert folds[0] == WalkForwardFold(
        index=0,
        warmup=WalkForwardInterval(0, 1),
        fit=WalkForwardInterval(1, 5),
        validation=WalkForwardInterval(6, 8),
        heldout=WalkForwardInterval(9, 11),
        split_id=folds[0].split_id,
    )
    assert folds[1].warmup == WalkForwardInterval(2, 3)
    assert folds[1].fit == WalkForwardInterval(3, 7)
    assert folds[1].validation == WalkForwardInterval(8, 10)
    assert folds[1].heldout == WalkForwardInterval(11, 13)
    assert all(len(fold.split_id) == 64 for fold in folds)
    assert len({fold.split_id for fold in folds}) == len(folds)


def test_expanding_schedule_keeps_initial_warmup_and_grows_fit_window() -> None:
    folds = WalkForwardSchedule(_timestamps(), _config(WalkForwardWindow.EXPANDING)).folds

    assert folds[0].warmup == folds[1].warmup == WalkForwardInterval(0, 1)
    assert folds[0].fit == WalkForwardInterval(1, 5)
    assert folds[1].fit == WalkForwardInterval(1, 7)
    assert folds[0].heldout.stop <= folds[1].heldout.start


def test_schedule_identity_changes_with_time_or_configuration() -> None:
    baseline = WalkForwardSchedule(_timestamps(), _config()).folds[0].split_id
    shifted = WalkForwardSchedule(_timestamps() + np.timedelta64(1, "h"), _config()).folds[0].split_id
    expanding = WalkForwardSchedule(
        _timestamps(), _config(WalkForwardWindow.EXPANDING)
    ).folds[0].split_id

    assert len({baseline, shifted, expanding}) == 3


def test_runner_keeps_fit_validation_and_heldout_data_separate() -> None:
    frame = _frame(11)
    runner = WalkForwardRunner(frame, timestamp_column="timestamp", config=_config())
    seen: dict[str, list[float]] = {}

    def fit(
        fold: WalkForwardFold,
        warmup: pl.DataFrame,
        training: pl.DataFrame,
    ) -> float:
        assert fold.index == 0
        seen["warmup"] = warmup["feature"].to_list()
        seen["fit"] = training["feature"].to_list()
        return float(training["target"].mean())

    def validate(
        _fold: WalkForwardFold,
        model: float,
        validation: pl.DataFrame,
    ) -> Mapping[str, float]:
        seen["validation"] = validation["feature"].to_list()
        return {"model": model, "rows": validation.height}

    def heldout(
        _fold: WalkForwardFold,
        model: float,
        evaluation: pl.DataFrame,
    ) -> Mapping[str, float]:
        seen["heldout"] = evaluation["feature"].to_list()
        return {"mean_error": float(evaluation["target"].mean()) - model}

    results = runner.run(fit, validate, heldout)

    assert seen == {
        "warmup": [0.0],
        "fit": [1.0, 2.0, 3.0, 4.0],
        "validation": [6.0, 7.0],
        "heldout": [9.0, 10.0],
    }
    assert results[0].validation_metrics == {"model": 5.0, "rows": 2.0}
    assert results[0].heldout_metrics == {"mean_error": 14.0}


def test_perturbing_heldout_rows_cannot_change_fitted_or_validation_values() -> None:
    first = _frame(11)
    changed = first.with_columns(
        pl.when(pl.col("feature") >= 9)
        .then(pl.lit(1_000_000.0))
        .otherwise(pl.col("target"))
        .alias("target")
    )

    def run(frame: pl.DataFrame) -> tuple[float, float]:
        runner = WalkForwardRunner(frame, timestamp_column="timestamp", config=_config())
        result = runner.run(
            lambda _fold, _warmup, fit: float(fit["target"].mean()),
            lambda _fold, model, _validation: {"model": model},
            lambda _fold, model, heldout: {"error": float(heldout["target"].mean()) - model},
        )[0]
        return result.validation_metrics["model"], result.heldout_metrics["error"]

    first_model, first_heldout = run(first)
    changed_model, changed_heldout = run(changed)

    assert first_model == changed_model == 5.0
    assert first_heldout != changed_heldout


def test_runner_owns_input_and_returns_detached_metric_mappings() -> None:
    frame = _frame(11)
    runner = WalkForwardRunner(frame, timestamp_column="timestamp", config=_config())
    frame[1, "target"] = 999.0
    validation_metrics = {"score": 1.0}

    result = runner.run(
        lambda _fold, _warmup, fit: float(fit["target"].mean()),
        lambda _fold, model, _validation: validation_metrics,
        lambda _fold, _model, _heldout: {"score": 2.0},
    )[0]
    validation_metrics["score"] = 999.0

    assert result.validation_metrics == {"score": 1.0}
    assert result.heldout_metrics == {"score": 2.0}


@pytest.mark.parametrize(
    "change",
    [
        {"fit_size": 0},
        {"validation_size": True},
        {"warmup_size": -1},
        {"refit_every": 1},
        {"window": "rolling"},
    ],
)
def test_config_rejects_invalid_or_overlapping_terms(change: dict[str, object]) -> None:
    values: dict[str, object] = {
        "fit_size": 4,
        "validation_size": 2,
        "heldout_size": 2,
        "warmup_size": 1,
        "purge_size": 1,
        "refit_every": 2,
        "window": WalkForwardWindow.ROLLING,
    }
    values.update(change)

    with pytest.raises((TypeError, ValueError)):
        WalkForwardConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("timestamps", "message"),
    [
        (_timestamps(10), "too short"),
        (np.array(["2024-01-01", "2024-01-01"], dtype="datetime64[D]"), "strictly increasing"),
        (np.array(["NaT"] * 11, dtype="datetime64[ns]"), "cannot contain NaT"),
    ],
)
def test_schedule_rejects_short_or_ambiguous_timelines(
    timestamps: np.ndarray,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        WalkForwardSchedule(timestamps, _config())


def test_runner_rejects_invalid_metrics_with_fold_context() -> None:
    runner = WalkForwardRunner(_frame(11), timestamp_column="timestamp", config=_config())

    with pytest.raises(ValueError, match="held-out metric 'score' for fold 0 must be finite"):
        runner.run(
            lambda _fold, _warmup, _fit: object(),
            lambda _fold, _model, _validation: {"score": 1.0},
            lambda _fold, _model, _heldout: {"score": float("nan")},
        )
