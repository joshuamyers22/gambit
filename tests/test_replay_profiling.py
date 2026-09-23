"""Diagnostic profiling must preserve results and cannot become qualification."""

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from gambit.tick_backtest import TopOfBookBacktester


def isolated_trial(output, spec, **options):
    """Keep the measured worker's parent independent of pytest coverage memory.

    Linux can carry the forking process's RSS high-water across exec. A small
    controller matches the standalone benchmark CLI and preserves the worker's
    unchanged 512 MiB bound, including its imports and disposable warmup.
    """
    script = (
        "import json,resource,sys\n"
        f"sys.path.insert(0, {str(Path(__file__).parents[1] / 'benchmarks')!r})\n"
        "import psutil\n"
        "from controlled_replay import run_trial\n"
        "payload=json.loads(sys.argv[1])\n"
        "trial=run_trial(payload['output'],payload['spec'],**payload['options'])\n"
        "print(json.dumps(dict(trial=trial,controller_rss=psutil.Process().memory_info().rss,"
        "controller_maxrss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)))\n"
    )
    result = subprocess.run([sys.executable, "-c", script,
                             json.dumps(dict(output=str(output), spec=spec, options=options))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    (output / "test-controller.json").write_text(json.dumps(record, indent=2) + "\n")
    trial = record["trial"]
    assert trial["status"] == "ok", (trial["failure_reason"], (output / "stderr.txt").read_text(), record)
    return trial


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


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("processed", [0, 65521, 946944000])
def test_batch_protocol_preserves_payload_and_immediate_flush(modules, monkeypatch, completed, processed):
    import io

    runner, _, _ = modules

    class Pipe(io.StringIO):
        def flush(self):
            self.flushed = self.getvalue()

    pipe = Pipe()
    monkeypatch.setattr(runner.sys, "stdout", pipe)
    monkeypatch.setattr(runner.time, "monotonic_ns", lambda: 123456789)
    runner.emit_batch_event(processed, completed=completed)
    assert pipe.getvalue() == pipe.flushed
    assert pipe.getvalue().endswith("\n")
    assert json.loads(pipe.flushed) == dict(phase="progress" if completed else "batch",
                                          at_ns=123456789, processed=processed)


@pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")
def test_optimized_batch_protocol_still_stops_native_hang(modules, tmp_path):
    runner, contract, _ = modules
    script = (
        "import signal,sys,time\n"
        f"sys.path.insert(0, {str(Path(runner.__file__).parent)!r})\n"
        "from controlled_replay import emit,emit_batch_event\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        "emit('harness',start_ns=time.monotonic_ns())\n"
        "emit_batch_event(65521,completed=True)\n"
        "emit_batch_event(65521,completed=False)\n"
        "time.sleep(10)\n"
    )
    result = runner.supervise([sys.executable, "-c", script], tmp_path,
                              dict(contract.LIMITS, setup=3, batch=.05, stop_grace=.05, cleanup=.5))
    assert result["status"] == "failed"
    assert result["failure_reason"] == "native batch timeout"
    assert result["failure_summary"]["last_reported_processed"] == 65521
    assert result["failure_summary"]["last_phase"] == "batch"
    assert result["shutdown"]["forced_kill"]
    assert result["shutdown"]["status"] == "terminated"


@pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")
def test_acceptance_profile_preserves_results_without_optional_stage_probes(modules, tmp_path):
    _, contract, _ = modules
    spec = contract.workload("fifo-smoke-v1")
    acceptance = isolated_trial(tmp_path / "acceptance", spec, session_id="acceptance-test")
    diagnosis = isolated_trial(tmp_path / "diagnostic", spec, session_id="acceptance-test",
                               diagnostic=dict(instrumentation="full"))
    assert acceptance["measurement_profile"] == "acceptance-v1"
    assert not acceptance["diagnostic"]
    assert diagnosis["measurement_profile"] == "diagnostic"
    assert acceptance["measurement"]["controls"] == diagnosis["measurement"]["controls"]
    a = json.loads((tmp_path / "acceptance/worker-result.json").read_text())
    d = json.loads((tmp_path / "diagnostic/worker-result.json").read_text())
    for stage in ("generation_seconds", "input_hash_seconds", "load_decode_seconds"):
        assert a["timing"][stage] is None
        assert d["timing"][stage] is not None
    assert a["batch_metrics"] is None
    assert a["timing"]["execution_seconds"] > 0
    assert acceptance["supervisor"]["rss_poll_count"] > 0


@pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")
def test_acceptance_worker_keeps_immediate_batch_watchdog(modules, tmp_path):
    runner, contract, _ = modules
    request = tmp_path / "request.json"
    request.write_text(json.dumps(dict(workload=contract.workload("fifo-smoke-v1"))))
    script = (
        "import sys,time\n"
        f"sys.path.insert(0, {str(Path(runner.__file__).parent)!r})\n"
        "import controlled_replay as r\n"
        "observed=r.identity()\nr.identity=lambda:observed\n"
        "original=r.TopOfBookBacktester\ncreated=0\n"
        "class Hung:\n def process_queue_batch(self,events): time.sleep(10)\n"
        "def engine(**config):\n global created\n created+=1\n"
        " return Hung() if created==2 else original(**config)\n"
        "r.TopOfBookBacktester=engine\n"
        f"raise SystemExit(r.worker_main({str(request)!r}))\n"
    )
    result = runner.supervise([sys.executable, "-c", script], tmp_path,
                              dict(contract.LIMITS, setup=3, progress=3, harness=3,
                                   batch=.05, stop_grace=.05, cleanup=.5))
    assert result["failure_reason"] == "native batch timeout", result
    assert result["failure_summary"]["last_phase"] == "batch"
    assert result["shutdown"]["forced_kill"]
    assert result["shutdown"]["status"] == "terminated"


@pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")
def test_probe_modes_and_python_profile_preserve_prefix_results(modules, tmp_path):
    runner, contract, _ = modules
    spec = contract.workload("fifo-smoke-v1")
    trials = []
    for mode in ("full", "timers_only"):
        trial = isolated_trial(tmp_path / mode, spec, session_id="diagnostic-test",
                               diagnostic=dict(instrumentation=mode, prefix_ticks=10003))
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
    profile = isolated_trial(tmp_path / "python", spec, session_id="diagnostic-test",
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
        trial = isolated_trial(path, spec, session_id="batch-metrics",
                               diagnostic=dict(batch_metrics=enabled))
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
