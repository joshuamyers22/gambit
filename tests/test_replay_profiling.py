"""Diagnostic profiling must preserve results and cannot become qualification."""

import copy
import importlib
import json
from pathlib import Path

import pytest

from gambit.tick_backtest import TopOfBookBacktester


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "benchmarks"))
    return tuple(importlib.import_module(name) for name in ("controlled_replay", "replay_contract", "profile_replay"))


@pytest.mark.parametrize("options", [False, [], {"prefix_ticks": 0}, {"prefix_ticks": True}, {"prefix_ticks": 100004},
                                    {"instrumentation": "unknown"}, {"cprofile": "yes"}, {"other": True}])
def test_diagnostic_admission_rejects_unbounded_or_unknown_modes(modules, options):
    runner, contract, _ = modules
    with pytest.raises(ValueError):
        runner.diagnostic_options(contract.workload("fifo-smoke-v1"), options)


@pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")
def test_probe_modes_and_python_profile_preserve_prefix_results(modules, tmp_path):
    runner, contract, _ = modules
    spec = contract.workload("fifo-smoke-v1")
    trials = []
    for mode in ("full", "timers_only"):
        trial = runner.run_trial(tmp_path / mode, spec, session_id="diagnostic-test",
                                 diagnostic=dict(instrumentation=mode, prefix_ticks=10003))
        assert trial["status"] == "ok", trial
        trials.append(trial)
    assert trials[0]["measurement"]["controls"] == trials[1]["measurement"]["controls"]
    report = json.loads((tmp_path / "timers_only/worker-result.json").read_text())
    assert report["measured_ticks"] == 10003
    assert report["resources"]["input_bytes"] == 10003 * 88
    assert report["timing"]["generation_seconds"] is None
    assert report["timing"]["input_hash_seconds"] is None
    assert not report["validation"]["canonical_controls_checked"]
    assert contract.summarize(trials)["timing_gate"] == "ineligible"
    assert "diagnostic profiling/probe modes cannot qualify" in contract.summarize(trials)["ineligibility_reasons"]
    profile = runner.run_trial(tmp_path / "python", spec, session_id="diagnostic-test",
                               diagnostic=dict(prefix_ticks=10003, cprofile=True))
    assert profile["measurement"]["controls"] == trials[0]["measurement"]["controls"]
    assert (tmp_path / "python/python-profile.pstats").stat().st_size > 0


@pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")
def test_fifo_batch_metrics_preserve_hashes_and_stay_ineligible(modules, tmp_path):
    runner, contract, _ = modules
    spec = contract.workload("fifo-smoke-v1")
    trials = []
    for enabled in (False, True):
        path = tmp_path / str(enabled)
        trial = runner.run_trial(path, spec, session_id="batch-metrics",
                                 diagnostic=dict(batch_metrics=enabled))
        assert trial["status"] == "ok", trial
        report = json.loads((path / "worker-result.json").read_text())
        if enabled:
            assert report["batch_metrics"]["records"] == spec["ticks"]
            assert report["batch_metrics"]["count"] == 2
            assert report["batch_metrics"]["total_ns"] / 1e9 == report["timing"]["processing_seconds"]
        else:
            assert report["batch_metrics"] is None
        assert trial["supervisor"]["shutdown"]["status"] == "exited"
        trials.append(trial)
    assert trials[0]["measurement"]["controls"] == trials[1]["measurement"]["controls"]
    assert contract.summarize(trials)["timing_gate"] == "ineligible"


def make_pairs(count=30, overhead=.02):
    rows = []
    for i in range(count):
        for mode in ("full", "timers_only"):
            rows.append(dict(status="ok", workload={"ticks": 1000}, source={}, identity={"binary": "same"},
                             diagnostic=dict(instrumentation=mode, prefix_ticks=1000),
                             measurement=dict(controls={"hash": "same"},
                                              execution_seconds=1 + overhead if mode == "full" else 1,
                                              harness_seconds=2 + overhead * 2 if mode == "full" else 2)))
    return rows


def test_overhead_sign_and_uncertainty_are_not_speedup_claims(modules):
    _, _, profiling = modules
    result = profiling.overhead_summary(make_pairs())
    assert result["diagnostic_only"]
    metric = result["metrics"]["execution_seconds"]
    assert metric["median_relative_overhead"] == pytest.approx(.02)
    assert metric["conclusion"] == "exceeds_1_percent"
    assert profiling.overhead_summary(make_pairs(overhead=.005))["metrics"]["execution_seconds"]["conclusion"] == "within_1_percent"
    assert profiling.overhead_summary(make_pairs(count=2))["metrics"]["execution_seconds"]["conclusion"] == "insufficient_pairs"


@pytest.mark.parametrize("change", ["controls", "identity", "failed", "prefix"])
def test_overhead_comparison_rejects_nonmatching_evidence(modules, change):
    _, _, profiling = modules
    rows = copy.deepcopy(make_pairs())
    if change == "failed":
        rows[0]["status"] = "failed"
    elif change == "prefix":
        rows[0]["diagnostic"]["prefix_ticks"] = 1
    elif change == "identity":
        rows[0]["identity"] = {"binary": "different"}
    else:
        rows[0]["measurement"]["controls"] = {"hash": "different"}
    with pytest.raises(ValueError):
        profiling.overhead_summary(rows)
