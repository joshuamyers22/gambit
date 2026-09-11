"""Check leak-runner fail-closed and scope rules without claiming local LSan."""

import ctypes
import os
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROBE = Path(__file__).with_name("native_numpy_allocator_probe.py")
RUNNER = Path(__file__).with_name("run_numpy_leak_check.py")
CONTROL = ("NumPy deliberate leak control exercised\n"
           "fault_malloc\n"
           "SUMMARY: AddressSanitizer: 16 byte(s) leaked in 1 allocation(s).\n")


@pytest.mark.parametrize("bootstrap", [None, lambda: 0])
def test_probe_rejects_missing_pre_interpreter_scope(monkeypatch, tmp_path, bootstrap):
    main = runpy.run_path(str(PROBE))["main"]
    monkeypatch.setattr(sys, "argv", [str(PROBE), "--build-dir", str(tmp_path / "build"), "--leak-check"])
    monkeypatch.setattr(ctypes, "CDLL", lambda *args: SimpleNamespace(gambit_lsan_bootstrapped=bootstrap))
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not (tmp_path / "build").exists()


@pytest.mark.parametrize("leaks", [0, 1])
def test_probe_does_not_double_disable_tracking_and_propagates_leaks(monkeypatch, tmp_path, leaks):
    main = runpy.run_path(str(PROBE))["main"]
    depth = 1  # The launcher already called __lsan_disable before Python startup.
    events = []

    def enable():
        nonlocal depth
        depth -= 1
        assert depth == 0, "native allocations must actually be tracked"

    def disable():
        nonlocal depth
        depth += 1
        assert depth == 1

    def exercise(directory, enable_tracking, disable_tracking):
        enable_tracking()
        events.append("tracked workload")
        disable_tracking()

    def check():
        assert events == ["tracked workload"] and depth == 1
        return leaks

    def exit_process(status):
        raise SystemExit(status)

    runtime = SimpleNamespace(gambit_lsan_bootstrapped=lambda: 1,
                              __lsan_enable=enable, __lsan_disable=disable,
                              __lsan_do_recoverable_leak_check=check)
    monkeypatch.setattr(ctypes, "CDLL", lambda *args: runtime)
    monkeypatch.setitem(main.__globals__, "build", lambda directory: None)
    monkeypatch.setitem(main.__globals__, "exercise", exercise)
    monkeypatch.setattr(os, "_exit", exit_process)
    monkeypatch.setattr(sys, "argv", [str(PROBE), "--build-dir", str(tmp_path), "--leak-check"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == leaks


def test_positive_control_cannot_run_in_ordinary_probe(monkeypatch, tmp_path):
    main = runpy.run_path(str(PROBE))["main"]
    monkeypatch.setattr(sys, "argv", [str(PROBE), "--build-dir", str(tmp_path), "--positive-control"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


@pytest.mark.parametrize("control,code,output", [
    (False, 0, "425 injected native failures\nLeakSanitizer result=0"),
    (True, 1, CONTROL),
])
def test_runner_accepts_only_verified_outcomes(control, code, output):
    verify = runpy.run_path(str(RUNNER))["verify_result"]
    verify(subprocess.CompletedProcess([], code, output, ""), control=control)


@pytest.mark.parametrize("control,code,output", [
    (False, 1, "425 injected native failures\nLeakSanitizer result=0"),
    (False, 0, "425 injected native failures"),
    (False, 0, "LeakSanitizer result=0"),
    (True, 0, CONTROL),
    (True, -11, CONTROL),
    (True, 1, "runtime missing"),
    (True, 1, CONTROL.replace("16 byte(s)", "32 byte(s)")),
    (True, 1, CONTROL.replace("fault_malloc", "unrelated_allocator")),
    (True, 1, CONTROL.replace("NumPy deliberate leak control exercised", "")),
])
def test_runner_rejects_crashes_missing_checks_and_unrelated_leaks(control, code, output):
    verify = runpy.run_path(str(RUNNER))["verify_result"]
    with pytest.raises(RuntimeError):
        verify(subprocess.CompletedProcess([], code, output, ""), control=control)


def test_runner_requires_both_child_results_and_forces_unsuppressed_tracking(monkeypatch, tmp_path):
    main = runpy.run_path(str(RUNNER))["main"]
    children = []

    def run(command, **kwargs):
        children.append(command)
        env = kwargs["env"]
        assert env["LSAN_OPTIONS"] == "print_suppressions=1"
        assert env["ASAN_OPTIONS"] == "detect_leaks=1:halt_on_error=1"
        assert env["LD_PRELOAD"] == "/asan.so:/stdc++.so"
        assert command[:2] == [str(tmp_path / "launcher"), sys.executable]
        assert "--leak-check" in command
        if "--positive-control" in command:
            return subprocess.CompletedProcess(command, 1, CONTROL, "")
        return subprocess.CompletedProcess(command, 0, "425 injected native failures\nLeakSanitizer result=0", "")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "argv", [str(RUNNER), "--build-dir", str(tmp_path)])
    monkeypatch.setenv("GAMBIT_SANITIZER_RUN", "1")
    monkeypatch.setenv("ASAN_LIBRARY", "/asan.so")
    monkeypatch.setenv("CXX_LIBRARY", "/stdc++.so")
    monkeypatch.setenv("ASAN_OPTIONS", "detect_leaks=0")
    monkeypatch.setenv("LSAN_OPTIONS", "suppressions=/broad.supp")
    monkeypatch.setitem(main.__globals__, "build_launcher", lambda directory: tmp_path / "launcher")
    monkeypatch.setattr(subprocess, "run", run)
    main()
    assert len(children) == 2
    assert "--positive-control" not in children[0] and "--positive-control" in children[1]
    assert "LeakSanitizer result=0" in (tmp_path / "probe.log").read_text()
    assert (tmp_path / "control.log").read_text() == CONTROL
