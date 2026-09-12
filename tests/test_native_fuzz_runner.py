"""Verify bounded fuzz invocation and evidence retention without faking fuzzing."""

import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "tools/run_native_fuzz.py"


@pytest.mark.parametrize("outcome", ["success", "crash", "timeout"])
def test_campaign_records_bounds_seed_and_diagnostics(monkeypatch, tmp_path, capsys, outcome):
    main = runpy.run_path(str(RUNNER))["main"]
    destination = tmp_path / "campaign"
    monkeypatch.setattr(sys, "argv", [str(RUNNER), "--output-dir", str(destination), "--format", "zip",
                                      "--seed", "42", "--runs", "1000000", "--seconds", "600"])
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("GITHUB_SHA", "test-source-commit")
    monkeypatch.setenv("LD_PRELOAD", "/unrelated/runtime.so")
    monkeypatch.setitem(main.__globals__, "libzip_flags", lambda: ["-lzip"])
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert "LD_PRELOAD" not in kwargs["env"]
        if len(calls) == 1:
            assert "-fsanitize=fuzzer,address,undefined" in command
            assert kwargs["timeout"] == 60
            return subprocess.CompletedProcess(command, 0)
        assert "-seed=42" in command and "-max_total_time=600" in command
        assert "-max_len=65536" in command and "-timeout=5" in command
        assert "-rss_limit_mb=512" in command and "-runs=1000000" in command
        assert kwargs["timeout"] == 615
        assert kwargs["env"]["ASAN_OPTIONS"] == "detect_leaks=1:halt_on_error=1"
        kwargs["stdout"].write("synthetic diagnostic\n")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 615)
        return subprocess.CompletedProcess(command, 1 if outcome == "crash" else 0)

    monkeypatch.setattr(subprocess, "run", run)
    if outcome == "success":
        main()
    else:
        error = subprocess.TimeoutExpired if outcome == "timeout" else subprocess.CalledProcessError
        with pytest.raises(error):
            main()
    assert len(calls) == 2
    record = json.loads((destination / "run.json").read_text())
    assert record["source_commit"] == "test-source-commit"
    assert record["seed"] == 42 and record["format"] == "zip"
    assert record["replay_only"] is False
    assert record["compiler_command"] == calls[0] and record["run_command"] == calls[1]
    assert (destination / "fuzz.log").read_text() == "synthetic diagnostic\n"
    assert len(list((destination / "corpus").iterdir())) == 12
    stdout = capsys.readouterr().out
    assert "synthetic diagnostic" in stdout
    assert ("Passed zip libFuzzer campaign" in stdout) == (outcome == "success")


@pytest.mark.parametrize("option,value", [("--runs", "0"), ("--seconds", "-1"),
                                         ("--seed", "0"), ("--seed", str(2**32))])
def test_invalid_campaign_budget_does_not_create_output(monkeypatch, tmp_path, option, value):
    main = runpy.run_path(str(RUNNER))["main"]
    destination = tmp_path / "invalid"
    monkeypatch.setattr(sys, "argv", [str(RUNNER), "--output-dir", str(destination), option, value])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not destination.exists()
