"""Regression checks for the lifetime probe itself, without pretending to run LSan."""

import ctypes
import os
import runpy
import sys
import weakref
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from gambit import _io

PROBE = Path(__file__).with_name("native_memory_probe.py")


def test_lifetime_workload_releases_final_arrays_before_returning():
    exercise = runpy.run_path(str(PROBE))["exercise"]
    references = []

    def observe(function, *args):
        result = function(*args)
        arrays = result if isinstance(result, list) else [result]
        references.extend(weakref.ref(array) for array in arrays if isinstance(array, np.ndarray))
        return result

    exercise(observe, iterations=2)
    assert len(references) >= 6, "the control must observe CSV and ZIP result buffers"
    assert all(reference() is None for reference in references)


def test_required_leak_runtime_cannot_silently_downgrade(monkeypatch, capsys):
    main = runpy.run_path(str(PROBE))["main"]
    monkeypatch.setattr(ctypes, "CDLL", lambda *args: SimpleNamespace())
    monkeypatch.setattr(sys, "argv", [str(PROBE), "--require-lsan"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "needs a loaded LeakSanitizer runtime" in capsys.readouterr().err


def test_leak_check_runs_only_after_final_csv_and_zip_arrays_are_released(monkeypatch):
    main = runpy.run_path(str(PROBE))["main"]
    references = []
    original = _io.read_file

    def observe(*args, **kwargs):
        arrays = original(*args, **kwargs)
        references.extend(weakref.ref(array) for array in arrays)
        return arrays

    def leak_check():
        assert len(references) >= 6
        assert all(reference() is None for reference in references)
        return 0

    def exit_process(status):
        raise SystemExit(status)

    runtime = SimpleNamespace(__lsan_disable=lambda: None, __lsan_enable=lambda: None,
                              __lsan_do_recoverable_leak_check=leak_check)
    monkeypatch.setattr(_io, "read_file", observe)
    monkeypatch.setattr(ctypes, "CDLL", lambda *args: runtime)
    monkeypatch.setattr(os, "_exit", exit_process)
    monkeypatch.setattr(sys, "argv", [str(PROBE), "--iterations", "2", "--require-lsan"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0


@pytest.mark.parametrize("iterations", ["0", "-1"])
def test_probe_rejects_nonpositive_iterations(monkeypatch, iterations):
    main = runpy.run_path(str(PROBE))["main"]
    monkeypatch.setattr(sys, "argv", [str(PROBE), "--iterations", iterations])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
