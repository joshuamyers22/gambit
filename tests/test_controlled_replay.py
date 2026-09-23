"""Native integration, failure containment, and truthful qualification boundaries."""

import copy
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gambit.tick_backtest import TopOfBookBacktester


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "benchmarks"))
    return importlib.import_module("controlled_replay")


@pytest.fixture
def contract(runner):
    return importlib.import_module("replay_contract")


def test_workload_admission_rejects_changes_to_policy(contract):
    for bad in (0, -1, 1048577, True):
        with pytest.raises(ValueError, match="chunk size"):
            contract.workload("fifo-3y-sparse-v1", bad)
    with pytest.raises(ValueError, match="smoke"):
        contract.workload("fifo-3y-sparse-v1", ticks=100)
    spec = contract.workload("fifo-smoke-v1")
    spec["configuration"]["audit_capacity"] = 2_000_000
    with pytest.raises(ValueError, match="configuration"):
        contract.validate_workload(spec)


def test_nearest_rank_quantiles_and_no_invented_extreme_tail(contract):
    result = contract.distribution([4., 1., 2., 3.])
    assert result == dict(count=4, p50=2., p95=4., p99=4., maximum=4., jitter=2., p99_9=None)
    assert contract.distribution([]) is None
    with pytest.raises(ValueError):
        contract.distribution([float("nan")])


def primary_trials(contract):
    return [dict(trial_id=f"trial-{i}", status="ok", session_id=f"session-{i // 50}", variant="candidate",
                 workload=contract.workload("fifo-3y-sparse-v1"), source=dict(kind="synthetic"),
                 identity=dict(binary="test"), build_attested=True, reference_host_matches=True,
                 measurement_profile="acceptance-v1",
                 session_evidence=dict(operator="test"),
                 measurement=dict(execution_seconds=4., harness_seconds=60., peak_rss_bytes=256 * 1024**2,
                                  controls=contract.EXPECTED["fifo-3y-sparse-v1"])) for i in range(200)]


def test_qualification_four_misses_pass_five_fail_without_promotion(contract):
    trials = primary_trials(contract)
    for row in trials[:4]:
        row["measurement"]["execution_seconds"] = 5.1
    report = contract.summarize(trials)
    assert report["timing_gate"] == "pass"
    assert report["qualification"] == "pending_independent_evidence"
    assert report["p99_9_qualified"] is False
    trials[4]["measurement"]["execution_seconds"] = 5.1
    assert contract.summarize(trials)["timing_gate"] == "fail"
    trials = primary_trials(contract)
    trials[0]["measurement"]["harness_seconds"] = 91.
    assert contract.summarize(trials)["timing_gate"] == "fail"


@pytest.mark.parametrize("mutation", ["failed", "host", "build", "duplicate", "session", "identity", "smoke", "hash",
                                     "missing_profile", "diagnostic_profile"])
def test_ineligible_evidence_cannot_qualify(contract, mutation):
    trials = primary_trials(contract)
    if mutation == "failed":
        trials[0]["status"] = "failed"
        trials[0]["measurement"] = None
    elif mutation == "host":
        trials[0]["reference_host_matches"] = False
    elif mutation == "build":
        trials[0]["build_attested"] = False
    elif mutation == "duplicate":
        trials[0]["trial_id"] = trials[1]["trial_id"]
    elif mutation == "session":
        trials[0]["session_id"] = "fifth-session"
    elif mutation == "identity":
        trials[0]["identity"] = dict(binary="other")
    elif mutation == "smoke":
        trials[0]["workload"] = contract.workload("fifo-smoke-v1")
    elif mutation == "missing_profile":
        del trials[0]["measurement_profile"]
    elif mutation == "diagnostic_profile":
        trials[0]["measurement_profile"] = "diagnostic"
    else:
        trials[0]["measurement"]["controls"] = dict(input_sha256="changed")
    report = contract.summarize(trials)
    assert report["timing_gate"] == "ineligible"
    assert report["ineligibility_reasons"]


@pytest.mark.performance
@pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")
def test_isolated_synthetic_and_storage_replay_match(runner, contract, tmp_path):
    spec = contract.workload("fifo-smoke-v1", chunk_size=4093, ticks=10003)
    dataset = tmp_path / "dataset"
    runner.prepare_dataset(dataset, spec)
    first = runner.run_trial(tmp_path / "synthetic", spec, session_id="test")
    other_chunk = dict(spec, chunk_size=8192)
    second = runner.run_trial(tmp_path / "storage", other_chunk, dataset=dataset, session_id="test")
    assert first["status"] == second["status"] == "ok", (first, second)
    assert first["measurement"]["controls"] == second["measurement"]["controls"]
    result = json.loads((tmp_path / "storage/worker-result.json").read_text())
    assert result["measurement_profile"] == second["measurement_profile"] == "acceptance-v1"
    assert result["timing"]["generation_seconds"] is None
    assert result["timing"]["load_decode_seconds"] is None
    assert second["measurement"]["harness_seconds"] >= result["timing"]["execution_seconds"]
    assert result["validation"]["full_volume_independent_trace_parity"] is False
    assert first["measurement"]["peak_rss_bytes"] <= contract.RSS_LIMIT
    with pytest.raises(FileExistsError):
        runner.run_trial(tmp_path / "synthetic", spec, session_id="retry")


@pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")
def test_storage_mutation_cannot_publish_a_success(runner, contract, tmp_path):
    spec = contract.workload("fifo-smoke-v1", ticks=1000)
    dataset = tmp_path / "dataset"
    runner.prepare_dataset(dataset, spec)
    # Mutate one valid bid-size byte: native input is still valid but digest must reject it.
    with (dataset / "events.bin").open("r+b") as stream:
        stream.seek(40)
        stream.write(b"\x01")
    result = runner.run_trial(tmp_path / "trial", spec, dataset=dataset, session_id="test")
    assert result["status"] == "failed"
    assert "checksum" in result["failure_reason"]
    assert result["measurement"] is None
    assert not (tmp_path / "trial/worker-result.json").exists()
    assert (tmp_path / "trial/trial.json").is_file()


def test_dataset_admission_and_exclusive_publication(runner, contract, tmp_path):
    spec = contract.workload("fifo-smoke-v1", ticks=3)
    dataset = tmp_path / "dataset"
    runner.prepare_dataset(dataset, spec)
    with pytest.raises(FileExistsError):
        runner.prepare_dataset(dataset, spec)
    with (dataset / "events.bin").open("ab") as stream:
        stream.write(b"x")
    with pytest.raises(ValueError, match="length"):
        runner.validate_dataset(dataset, spec)
    oversized = tmp_path / "large.json"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        contract.read_json(oversized)


def fake_worker(tmp_path, code):
    path = tmp_path / "worker.py"
    path.write_text("import time, json, signal\n" + code)
    return [sys.executable, str(path)]


@pytest.mark.parametrize("kind", ["batch", "setup", "rss", "cancel", "incomplete", "protocol"])
def test_supervisor_stops_and_reaps_failed_workers(runner, contract, tmp_path, kind):
    limits = dict(contract.LIMITS, batch=.05, setup=.3, stop_grace=.05, cleanup=.5)
    code = "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    cancel = None
    if kind == "batch":
        code += 'print(json.dumps(dict(phase="harness", at_ns=time.monotonic_ns(), start_ns=time.monotonic_ns())), flush=True)\n'
        code += 'print(json.dumps(dict(phase="batch", at_ns=time.monotonic_ns())), flush=True)\n'
    elif kind == "rss":
        limits["rss"] = 1024
    elif kind == "cancel":
        cancel = tmp_path / "stop"
        cancel.touch()
    elif kind == "protocol":
        code += 'print("not-json", flush=True)\n'
    if kind != "incomplete":
        code += "time.sleep(10)\n"
    result = runner.supervise(fake_worker(tmp_path, code), tmp_path, limits, cancel)
    assert result["status"] == "failed"
    assert result["returncode"] is not None
    assert result["failure_summary"]["partial_progress_only"]
    assert result["shutdown"]["status"] in ("terminated", "exited")
    assert result["shutdown"]["cleanup_limit_seconds"] == .55
    if kind != "incomplete":
        assert result["cleanup"]["exited"]
    assert result["process_seconds"] < 3
    if kind == "batch":
        assert "batch timeout" in result["failure_reason"]
        assert result["shutdown"]["forced_kill"]


def test_watchdog_retains_last_confirmed_progress_on_native_hang(runner, contract, tmp_path):
    limits = dict(contract.LIMITS, batch=.05, setup=.3, stop_grace=.05, cleanup=.5)
    code = 'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
    code += 'print(json.dumps(dict(phase="harness", at_ns=time.monotonic_ns(), start_ns=time.monotonic_ns())), flush=True)\n'
    code += 'print(json.dumps(dict(phase="progress", at_ns=time.monotonic_ns(), processed=123)), flush=True)\n'
    code += 'print(json.dumps(dict(phase="batch", at_ns=time.monotonic_ns())), flush=True)\n'
    code += 'time.sleep(10)\n'
    result = runner.supervise(fake_worker(tmp_path, code), tmp_path, limits)
    assert result["status"] == "failed"
    assert result["failure_summary"]["last_reported_processed"] == 123
    assert result["failure_summary"]["last_phase"] == "batch"
    assert result["shutdown"]["forced_kill"]
    assert result["shutdown"]["cleanup_seconds"] < 1


def test_protocol_traffic_does_not_multiply_resource_queries(runner, contract, tmp_path, monkeypatch):
    process_type = runner.psutil.Process
    queries = []

    class ObservedProcess:
        def __init__(self, pid):
            self.process = process_type(pid)

        def memory_info(self):
            queries.append(runner.time.monotonic_ns())
            return self.process.memory_info()

    monkeypatch.setattr(runner.psutil, "Process", ObservedProcess)
    code = 'print(json.dumps(dict(phase="harness", at_ns=time.monotonic_ns(), start_ns=time.monotonic_ns())), flush=True)\n'
    code += 'for n in range(1000):\n'
    code += ' print(json.dumps(dict(phase="progress", at_ns=time.monotonic_ns(), processed=n)), flush=True)\n'
    code += ' time.sleep(.0002)\n'
    code += 'print(json.dumps(dict(phase="complete", at_ns=time.monotonic_ns(), peak_rss_bytes=0)), flush=True)\n'
    result = runner.supervise(fake_worker(tmp_path, code), tmp_path, contract.LIMITS)
    assert result["status"] == "ok", result
    assert len(queries) == result["rss_poll_count"]
    assert 2 <= len(queries) < 100
    assert result["rss_poll_interval_seconds"] == .02
    assert result["shutdown"]["status"] == "exited"


def test_exited_worker_cannot_leave_helper_holding_protocol_pipe_open(runner, contract, tmp_path):
    # The parent exits immediately; its child inherits stdout and ignores TERM.
    # Run the supervisor in an outer subprocess so a regression cannot hang pytest.
    import subprocess

    script = tmp_path / "exercise.py"
    script.write_text(
        "import sys, json\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(runner.__file__).parent)!r})\n"
        "from controlled_replay import supervise\nfrom replay_contract import LIMITS\n"
        "code = \"import subprocess,sys; subprocess.Popen([sys.executable,'-c',"
        "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(5)'])\"\n"
        f"result=supervise([sys.executable,'-c',code],Path({str(tmp_path)!r}),"
        "dict(LIMITS,setup=.15,stop_grace=.05,cleanup=.1))\n"
        "print(json.dumps(result))\n"
    )
    completed = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                               timeout=3, check=True)
    report = json.loads(completed.stdout)
    assert report["status"] == "failed"
    assert report["shutdown"]["status"] == "terminated"
    assert report["cleanup"]["exited"]
    assert report["process_seconds"] < 1


def test_build_provenance_cannot_be_inferred_or_mismatch(runner):
    observed = dict(native_extension_sha256="abc", files={"src/gambit/cpp/factor_cache/top_of_book_backtest.cpp": "src"})
    assert runner.build_attested(None, observed) is False
    manifest = dict(compiler_version="Apple clang-1700.0.13.5", compile_commands=[["clang++", "-std=c++11", "-O3"]],
                    link_commands=[["clang++", "-shared"]], native_extension_sha256="abc", source_sha256="src")
    assert runner.build_attested(manifest, observed)
    invalid = copy.deepcopy(manifest)
    invalid["native_extension_sha256"] = "different"
    with pytest.raises(ValueError, match="imported"):
        runner.build_attested(invalid, observed)
    invalid = copy.deepcopy(manifest)
    invalid["compile_commands"][0].append("-ffast-math")
    with pytest.raises(ValueError, match="flags"):
        runner.build_attested(invalid, observed)


def test_session_conditions_require_current_quiet_interval(runner, tmp_path):
    now = datetime.now(timezone.utc)
    evidence = dict(operator="tester", quiet_period_start_utc=(now - timedelta(minutes=6)).isoformat(),
                    quiet_period_end_utc=(now - timedelta(seconds=30)).isoformat(), background_workloads="none",
                    power_thermal_notes="AC, normal", independence_notes="separate sessions",
                    ac_power=True, low_power_mode=False)
    path = tmp_path / "conditions.json"
    path.write_text(json.dumps(evidence))
    assert runner.session_evidence(path) == evidence
    evidence["quiet_period_end_utc"] = (now + timedelta(minutes=1)).isoformat()
    path.write_text(json.dumps(evidence))
    with pytest.raises(ValueError, match="before this session"):
        runner.session_evidence(path)


def test_paired_screen_requires_matching_outputs_and_resource_guardrails(runner, contract):
    trials = primary_trials(contract)[:30]
    for t in trials:
        t["variant"] = "baseline"
        t["identity"] = dict(host="same-host")
    candidates = copy.deepcopy(trials)
    for t in candidates:
        t["variant"] = "candidate"
        t["measurement"]["execution_seconds"] *= .8
    paired = [t for pair in zip(trials, candidates) for t in pair]
    result = runner.compare_pairs(paired)
    assert result["status"] == "retention_signal"
    assert result["paired_bootstrap_95_interval"][0] > 0
    candidates[0]["measurement"]["controls"] = dict(input_sha256="wrong")
    assert runner.compare_pairs(paired)["status"] == "inconclusive"
    candidates[0]["measurement"]["controls"] = trials[0]["measurement"]["controls"]
    for row in candidates:
        row["measurement"]["peak_rss_bytes"] *= 2
    assert runner.compare_pairs(paired)["status"] == "no_retention_signal"


def test_aggregation_rejects_incomplete_or_duplicate_campaigns(runner, contract, tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    contract.write_json(campaign / "campaign.json", dict(planned_trials=2, paired=False))
    folder = campaign / "001-candidate"
    folder.mkdir()
    contract.write_json(folder / "trial.json", primary_trials(contract)[0])
    with pytest.raises(ValueError, match="complete"):
        runner.aggregate([campaign], tmp_path / "summary.json")
    (campaign / "campaign.json").write_text(json.dumps(dict(planned_trials=1, paired=False)))
    with pytest.raises(ValueError, match="duplicate"):
        runner.aggregate([campaign, campaign], tmp_path / "summary.json")


def test_dataset_write_failure_never_commits_a_manifest(runner, contract, monkeypatch, tmp_path):
    def failed_generation(*args, **kwargs):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(runner, "make_queue_events", failed_generation)
    with pytest.raises(OSError, match="simulated"):
        runner.prepare_dataset(tmp_path / "broken", contract.workload("fifo-smoke-v1", ticks=2))
    assert not (tmp_path / "broken/manifest.json").exists()
    assert (tmp_path / "broken/failure.json").exists()
