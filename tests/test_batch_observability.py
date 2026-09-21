import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def screen(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "benchmarks"))
    return importlib.import_module("batch_observability")


def rows(count=30):
    result = []
    for _ in range(count):
        row = {}
        for mode in ("off", "on"):
            row[mode] = dict(status="ok", diagnostics=mode == "on", native_sha256="native",
                             input_sha256="input", python="python", platform="platform",
                             cases={"test": dict(status="ok", path="in_place", count=1024,
                                                 batch=32, capacity=64, rate=0, stall_seconds=0,
                                                 shutdown={"status": "joined"}, snapshot={"processed": 1024},
                                                 wall_ns=100 if mode == "off" else 105,
                                                 cpu_ns=100, peak_rss_bytes=100)})
        result.append(row)
    return result


def test_probe_overhead_is_not_qualification(screen):
    summary = screen.summarize(rows())
    assert summary["performance_qualification"] is False
    metric = summary["cases"]["test"]["wall_ns"]
    assert metric["median_relative_overhead"] == pytest.approx(.05)
    assert metric["one_percent_target"] == "exceeds"
    assert screen.summarize(rows(2))["cases"]["test"]["wall_ns"]["one_percent_target"] == "insufficient_pairs"


@pytest.mark.parametrize("change", ["native", "snapshot", "failed", "shutdown", "mode", "case", "workload", "timing"])
def test_bad_or_mixed_evidence_cannot_supply_an_overhead_result(screen, change):
    values = copy.deepcopy(rows())
    target = values[0]["on"]
    if change == "native":
        target["native_sha256"] = "other"
    elif change == "snapshot":
        target["cases"]["test"]["snapshot"] = {}
    elif change == "failed":
        target["status"] = "failed"
    elif change == "shutdown":
        target["cases"]["test"]["shutdown"]["status"] = "incomplete"
    elif change == "mode":
        target["diagnostics"] = False
    elif change == "case":
        target["cases"] = {}
    elif change == "workload":
        for mode in ("off", "on"):
            values[0][mode]["cases"]["test"]["batch"] = 64
    else:
        target["cases"]["test"]["wall_ns"] = float("nan")
    with pytest.raises(ValueError):
        screen.summarize(values)
