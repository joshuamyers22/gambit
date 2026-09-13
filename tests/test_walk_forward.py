from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from gambit.covariance_risk import CovarianceRiskModel
from gambit.optimize import (
    WalkForwardConfig,
    WalkForwardExperimentResult,
    WalkForwardFold,
    WalkForwardHeldoutEvaluation,
    WalkForwardInterval,
    WalkForwardRunner,
    WalkForwardSchedule,
    WalkForwardTrainingSet,
    WalkForwardWindow,
)
from gambit.var_risk import TailRiskModel
from gambit.walk_forward_io import WalkForwardResultError

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


def _optimization_frame(size: int = 18) -> pl.DataFrame:
    return _frame(size).with_columns((pl.col("target") * 1_000.0).alias("future_only"))


def _optimization_parameters(
    _fold: WalkForwardFold,
    _seed: int,
) -> Iterable[dict[str, Any]]:
    return [
        {"offset": 8.0, "label": "zeta"},
        {"offset": 0.0, "label": "baseline"},
        {"offset": 8.0, "label": "alpha"},
    ]


def _optimization_parameters_with_failure(
    fold: WalkForwardFold,
    seed: int,
) -> Iterable[dict[str, Any]]:
    return [*_optimization_parameters(fold, seed), {"offset": 4.0, "label": "failure"}]


def _fit_optimization_candidate(
    _fold: WalkForwardFold,
    parameters: Mapping[str, Any],
    training: WalkForwardTrainingSet,
    seed: int,
) -> tuple[float, int]:
    if parameters.get("label") == "failure":
        raise ArithmeticError("deliberate candidate failure")
    assert training.columns == ("timestamp", "target")
    assert training.warmup_frame().columns == training.fit_frame().columns == ["timestamp", "target"]
    prediction = float(training.fit_frame()["target"].mean()) + float(parameters["offset"])
    return prediction, seed


def _validate_optimization_candidate(
    _fold: WalkForwardFold,
    _parameters: Mapping[str, Any],
    model: tuple[float, int],
    validation: pl.DataFrame,
    seed: int,
) -> tuple[float, Mapping[str, float]]:
    assert model[1] == seed
    error = abs(float(validation["target"].mean()) - model[0])
    return error, {"absolute_error": error}


def _evaluate_optimization_candidate(
    _fold: WalkForwardFold,
    _parameters: Mapping[str, Any],
    model: tuple[float, int],
    heldout: pl.DataFrame,
    seed: int,
) -> WalkForwardHeldoutEvaluation:
    assert model[1] == seed
    errors = heldout["target"] - model[0]
    return WalkForwardHeldoutEvaluation(
        {"mean_error": float(errors.mean())},
        heldout.select("timestamp").with_columns(errors.cum_sum().alias("equity")),
    )


def _model_fingerprint(
    _fold: WalkForwardFold,
    parameters: Mapping[str, Any],
    model: tuple[float, int],
    seed: int,
) -> str:
    payload = json.dumps(
        {"parameters": dict(parameters), "prediction": model[0], "seed": seed},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _optimize(
    frame: pl.DataFrame,
    *,
    max_processes: int,
) -> WalkForwardExperimentResult:
    runner = WalkForwardRunner(frame, timestamp_column="timestamp", config=_config())
    return runner.optimize(
        _optimization_parameters,
        _fit_optimization_candidate,
        _validate_optimization_candidate,
        _evaluate_optimization_candidate,
        _model_fingerprint,
        fit_columns=["timestamp", "target"],
        seed=314159,
        max_processes=max_processes,
        process_start_method="spawn",
        max_pending_tasks=2,
    )


def _optimize_with_failure(*, max_processes: int = 1) -> WalkForwardExperimentResult:
    runner = WalkForwardRunner(_optimization_frame(11), timestamp_column="timestamp", config=_config())
    return runner.optimize(
        _optimization_parameters_with_failure,
        _fit_optimization_candidate,
        _validate_optimization_candidate,
        _evaluate_optimization_candidate,
        _model_fingerprint,
        fit_columns=["timestamp", "target"],
        seed=2718,
        max_processes=max_processes,
        process_start_method="spawn",
        max_pending_tasks=2,
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
    expanding = WalkForwardSchedule(_timestamps(), _config(WalkForwardWindow.EXPANDING)).folds[0].split_id

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
        pl.when(pl.col("feature") >= 9).then(pl.lit(1_000_000.0)).otherwise(pl.col("target")).alias("target")
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


def test_optimized_runner_selects_on_validation_then_refits_before_heldout() -> None:
    result = _optimize(_optimization_frame(11), max_processes=1)[0]

    assert result.selected_parameters == {"offset": 8.0, "label": "alpha"}
    assert result.validation_cost == 0.0
    assert result.validation_metrics == {"absolute_error": 0.0}
    assert result.heldout_metrics == {"mean_error": 6.0}
    assert [trial.parameters["label"] for trial in result.trials] == ["alpha", "baseline", "zeta"]


def test_optimized_fit_allowlist_excludes_future_only_columns_and_heldout_changes() -> None:
    baseline = _optimization_frame(11)
    perturbed = baseline.with_columns(
        pl.when(pl.col("feature") >= 9).then(pl.lit(1_000_000.0)).otherwise(pl.col("target")).alias("target")
    )

    first = _optimize(baseline, max_processes=1)[0]
    changed = _optimize(perturbed, max_processes=1)[0]

    assert first.selected_parameters == changed.selected_parameters
    assert first.validation_cost == changed.validation_cost
    assert first.validation_metrics == changed.validation_metrics
    assert first.trials == changed.trials
    assert first.heldout_metrics != changed.heldout_metrics


def test_seeded_sequential_and_existing_process_scheduler_results_agree() -> None:
    frame = _optimization_frame(11)

    sequential = _optimize(frame, max_processes=1)
    parallel = _optimize(frame, max_processes=2)

    assert sequential == parallel


def test_fold_seeds_are_reproducible_and_split_specific() -> None:
    first = _optimize(_optimization_frame(), max_processes=1)
    repeated = _optimize(_optimization_frame(), max_processes=1)

    assert [result.seed for result in first] == [result.seed for result in repeated]
    assert len({result.seed for result in first}) == len(first)


def test_optimized_result_owns_hashes_and_chronological_out_of_sample_equity() -> None:
    result = _optimize(_optimization_frame(), max_processes=1)

    expected_timestamps = np.concatenate(
        [_timestamps()[fold.fold.heldout.start : fold.fold.heldout.stop] for fold in result]
    ).astype("datetime64[ns]")
    assert len(result.input_sha256) == 64
    assert {fold.input_sha256 for fold in result} == {result.input_sha256}
    assert all(len(fold.model_sha256) == 64 for fold in result)
    assert result.out_of_sample_equity.columns == ["timestamp", "equity"]
    assert np.array_equal(result.out_of_sample_equity["timestamp"].to_numpy(), expected_timestamps)

    detached = result.out_of_sample_equity
    detached[0, "equity"] = 1_000_000.0
    assert result.out_of_sample_equity[0, "equity"] != 1_000_000.0


def test_input_identity_changes_without_leaking_heldout_changes_into_selection() -> None:
    baseline = _optimization_frame(11)
    changed = baseline.with_columns(
        pl.when(pl.col("feature") >= 9).then(pl.lit(999.0)).otherwise(pl.col("target")).alias("target")
    )

    first = _optimize(baseline, max_processes=1)
    second = _optimize(changed, max_processes=1)

    assert first.input_sha256 != second.input_sha256
    assert first[0].selected_parameters == second[0].selected_parameters
    assert first[0].model_sha256 == second[0].model_sha256


def test_failed_trials_are_retained_with_seeded_process_parity() -> None:
    sequential = _optimize_with_failure(max_processes=1)
    parallel = _optimize_with_failure(max_processes=2)

    assert sequential == parallel
    assert len(sequential[0].trials) == 3
    assert len(sequential[0].failures) == 1
    assert sequential[0].failures[0].parameters["label"] == "failure"
    assert sequential[0].failures[0].error_type == "ArithmeticError"
    assert sequential[0].failures[0].message == "deliberate candidate failure"


def test_experiment_artifact_round_trip_retains_all_evidence(tmp_path: Path) -> None:
    result = _optimize_with_failure()
    destination = tmp_path / "walk-forward-result"

    assert result.save(destination) == destination
    restored = WalkForwardExperimentResult.load(destination)

    assert restored == result
    assert restored[0].trials == result[0].trials
    assert restored[0].failures == result[0].failures
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["format"] == "gambit.walk-forward-experiment"
    assert manifest["version"] == 1
    assert manifest["folds"][0]["split_id"] == result[0].fold.split_id
    assert manifest["folds"][0]["model_sha256"] == result[0].model_sha256
    assert manifest["folds"][0]["failures"][0]["error_type"] == "ArithmeticError"

    with pytest.raises(FileExistsError):
        result.save(destination)


def test_experiment_artifact_rejects_checksum_and_version_changes(tmp_path: Path) -> None:
    destination = tmp_path / "walk-forward-result"
    _optimize(_optimization_frame(11), max_processes=1).save(destination)
    equity_path = destination / "equity.arrow"
    equity_path.write_bytes(equity_path.read_bytes() + b"changed")

    with pytest.raises(WalkForwardResultError, match="checksum mismatch"):
        WalkForwardExperimentResult.load(destination)

    replacement = tmp_path / "unsupported-result"
    _optimize(_optimization_frame(11), max_processes=1).save(replacement)
    manifest_path = replacement / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = 999
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(WalkForwardResultError, match="unsupported"):
        WalkForwardExperimentResult.load(replacement)


def test_optimized_runner_rejects_unaligned_equity_and_invalid_model_identity() -> None:
    runner = WalkForwardRunner(_optimization_frame(11), timestamp_column="timestamp", config=_config())

    def shifted_equity(
        fold: WalkForwardFold,
        parameters: Mapping[str, Any],
        model: tuple[float, int],
        heldout: pl.DataFrame,
        seed: int,
    ) -> WalkForwardHeldoutEvaluation:
        evaluation = _evaluate_optimization_candidate(fold, parameters, model, heldout, seed)
        return WalkForwardHeldoutEvaluation(
            evaluation.metrics,
            evaluation.equity.with_columns(pl.col("timestamp") + pl.duration(days=1)),
        )

    with pytest.raises(ValueError, match="must exactly match held-out rows"):
        runner.optimize(
            _optimization_parameters,
            _fit_optimization_candidate,
            _validate_optimization_candidate,
            shifted_equity,
            _model_fingerprint,
            fit_columns=["timestamp", "target"],
        )

    with pytest.raises(ValueError, match="must be a lowercase SHA-256 digest"):
        runner.optimize(
            _optimization_parameters,
            _fit_optimization_candidate,
            _validate_optimization_candidate,
            _evaluate_optimization_candidate,
            lambda _fold, _parameters, _model, _seed: "not-a-sha",
            fit_columns=["timestamp", "target"],
        )


def test_optimized_runner_rejects_empty_sources_and_invalid_fit_columns() -> None:
    runner = WalkForwardRunner(_optimization_frame(11), timestamp_column="timestamp", config=_config())

    with pytest.raises(ValueError, match="fit_columns cannot contain duplicates"):
        runner.optimize(
            _optimization_parameters,
            _fit_optimization_candidate,
            _validate_optimization_candidate,
            _evaluate_optimization_candidate,
            _model_fingerprint,
            fit_columns=["target", "target"],
        )

    with pytest.raises(ValueError, match="must include the timestamp column"):
        runner.optimize(
            _optimization_parameters,
            _fit_optimization_candidate,
            _validate_optimization_candidate,
            _evaluate_optimization_candidate,
            _model_fingerprint,
            fit_columns=["target"],
        )

    with pytest.raises(ValueError, match="produced no successful trials for fold 0; 0 failed"):
        runner.optimize(
            lambda _fold, _seed: (),
            _fit_optimization_candidate,
            _validate_optimization_candidate,
            _evaluate_optimization_candidate,
            _model_fingerprint,
            fit_columns=["timestamp", "target"],
        )

    with pytest.raises(ValueError, match="produced no successful trials for fold 0; 1 failed"):
        runner.optimize(
            lambda _fold, _seed: ({"offset": 4.0, "label": "failure"},),
            _fit_optimization_candidate,
            _validate_optimization_candidate,
            _evaluate_optimization_candidate,
            _model_fingerprint,
            fit_columns=["timestamp", "target"],
        )


def test_training_set_fits_builtin_risk_models_only_on_fit_interval() -> None:
    runner = WalkForwardRunner(_optimization_frame(11), timestamp_column="timestamp", config=_config())
    observed: list[tuple[np.datetime64, np.datetime64, float]] = []

    def fit_risk_models(
        fold: WalkForwardFold,
        parameters: Mapping[str, Any],
        training: WalkForwardTrainingSet,
        seed: int,
    ) -> tuple[float, int]:
        covariance = training.fit_covariance(
            CovarianceRiskModel(lookback=4, min_observations=2),
            symbols=["target"],
        )
        tail_risk = training.fit_tail_risk(
            TailRiskModel(lookback=4, min_observations=2),
            symbols=["target"],
        )
        fitted_mean = training.fit_estimator(lambda frame: float(frame["target"].mean()))
        detached = training.fit_frame()
        detached[0, "target"] = 999.0
        assert training.fit_frame()[0, "target"] == 2.0
        observed.append((covariance.as_of, tail_risk.as_of, fitted_mean))
        return _fit_optimization_candidate(fold, parameters, training, seed)

    runner.optimize(
        _optimization_parameters,
        fit_risk_models,
        _validate_optimization_candidate,
        _evaluate_optimization_candidate,
        _model_fingerprint,
        fit_columns=["timestamp", "target"],
        seed=7,
        max_processes=1,
    )

    expected_as_of = np.datetime64("2024-01-05", "ns")
    assert observed == [(expected_as_of, expected_as_of, 5.0)] * 4
