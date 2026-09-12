"""Deterministic controls for the IPC fuzzer; these are not a fuzz campaign."""

import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

import ipc_fuzz_target as target

ROOT = Path(__file__).resolve().parents[1]


def test_all_synthetic_valid_profiles_are_admitted_without_native_decoding(monkeypatch):
    seeds = target.seed_inputs()

    def no_decode(*args, **kwargs):
        pytest.fail("the IPC preflight fuzzer must not decode mutated native payloads")

    monkeypatch.setattr(pl, "read_ipc", no_decode)
    assert len(seeds) == 19
    assert not any(target.test_one_input(seed) for seed in seeds[:3])
    assert all(target.test_one_input(seed) for seed in seeds[3:])


def test_only_documented_rejections_are_swallowed(monkeypatch):
    def unexpected(*args):
        raise RuntimeError("unexpected parser bug")

    monkeypatch.setattr(target, "inspect_ipc", unexpected)
    with pytest.raises(RuntimeError, match="unexpected parser bug"):
        target.test_one_input(b"\x00\x00input")


@pytest.mark.parametrize("decoded", [-1, target.LIMITS.max_table_decoded_bytes + 1])
def test_admitted_payload_budget_is_an_oracle(monkeypatch, decoded):
    monkeypatch.setattr(target, "inspect_ipc", lambda *args: decoded)
    with pytest.raises(AssertionError, match="out-of-budget"):
        target.test_one_input(b"\x00\x00input")


@pytest.mark.parametrize("payload", [b"", b"\x00", b"x" * (target.MAX_INPUT_BYTES + 1)])
def test_oversized_or_short_inputs_never_enter_parser(monkeypatch, payload):
    def forbidden(*args):
        pytest.fail("invalid input envelope reached the parser")

    monkeypatch.setattr(target, "inspect_ipc", forbidden)
    assert not target.test_one_input(payload)


@pytest.mark.integration
def test_explicit_seed_replay_runs_without_atheris(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "tools/run_ipc_fuzz.py"),
                             "--output-dir", str(tmp_path / "replay"), "--replay-only"],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "19 inputs (NOT coverage-guided)" in result.stdout
    assert json.loads((tmp_path / "replay/run.json").read_text())["native_decoder"] is False


def test_worker_requires_fuzz_engine_unless_replay_is_explicit(monkeypatch):
    main = runpy.run_path(str(ROOT / "tests/ipc_fuzz_probe.py"))["main"]
    monkeypatch.setattr(sys, "argv", ["ipc_fuzz_probe.py"])
    monkeypatch.setitem(sys.modules, "atheris", None)
    with pytest.raises(ModuleNotFoundError):
        main()


def test_empty_seed_replay_cannot_claim_success(monkeypatch):
    main = runpy.run_path(str(ROOT / "tests/ipc_fuzz_probe.py"))["main"]
    monkeypatch.setattr(sys, "argv", ["ipc_fuzz_probe.py", "--replay-only"])
    with pytest.raises(SystemExit, match="at least one input"):
        main()


def test_worker_instruments_validator_and_target_before_fuzzing(monkeypatch):
    main = runpy.run_path(str(ROOT / "tests/ipc_fuzz_probe.py"))["main"]
    events = []

    functions = []

    def instrument(function):
        functions.append(function)
        assert function.__module__ in ("gambit.ipc_validation", "ipc_fuzz_target")
        return function

    def setup(argv, function, *, internal_libfuzzer):
        assert len(functions) >= 15
        assert {item.__name__ for item in functions} >= {"test_one_input", "inspect_ipc", "_batch", "span", "vector"}
        assert function is target.test_one_input and internal_libfuzzer is True
        events.append("setup")

    monkeypatch.setitem(sys.modules, "atheris", SimpleNamespace(
        instrument_func=instrument, Setup=setup, Fuzz=lambda: events.append("fuzz")))
    monkeypatch.setattr(sys, "argv", ["ipc_fuzz_probe.py", "corpus", "-runs=10000"])
    main()
    assert events == ["setup", "fuzz"]


@pytest.mark.parametrize("outcome", ["success", "crash", "timeout", "no_coverage"])
def test_campaign_bounds_and_failure_propagation(monkeypatch, tmp_path, capsys, outcome):
    main = runpy.run_path(str(ROOT / "tools/run_ipc_fuzz.py"))["main"]
    output = tmp_path / "campaign"
    monkeypatch.setattr(sys, "argv", ["run_ipc_fuzz.py", "--output-dir", str(output)])

    def run(command, **kwargs):
        assert "-runs=10000" in command and "-max_total_time=30" in command
        assert "-max_len=65536" in command and "-timeout=5" in command
        assert "-rss_limit_mb=512" in command and kwargs["timeout"] == 60
        kwargs["stdout"].write("cov: 50\nDone 10000 runs\n" if outcome == "success" else "diagnostic\n")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 60)
        return subprocess.CompletedProcess(command, 1 if outcome == "crash" else 0)

    monkeypatch.setattr(subprocess, "run", run)
    if outcome == "success":
        main()
    else:
        exception = {"crash": subprocess.CalledProcessError, "timeout": subprocess.TimeoutExpired,
                     "no_coverage": RuntimeError}[outcome]
        with pytest.raises(exception):
            main()
    assert (output / "run.json").exists() and (output / "fuzz.log").exists()
    assert ("Passed IPC coverage-guided" in capsys.readouterr().out) == (outcome == "success")


@pytest.mark.parametrize("option,value", [("--runs", "0"), ("--seconds", "0"),
                                         ("--seed", "0"), ("--seed", str(2**32))])
def test_invalid_campaign_limits_do_not_create_output(monkeypatch, tmp_path, option, value):
    main = runpy.run_path(str(ROOT / "tools/run_ipc_fuzz.py"))["main"]
    output = tmp_path / "invalid"
    monkeypatch.setattr(sys, "argv", ["run_ipc_fuzz.py", "--output-dir", str(output), option, value])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not output.exists()
