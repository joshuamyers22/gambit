"""Input speedups must survive complete-harness and regression gates."""

import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def comparison(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "benchmarks"))
    return importlib.import_module("input_comparison")


def pairs():
    baseline = dict(status="ok", workload={"ticks": 1000}, diagnostic={},
                    source=dict(kind="synthetic", manifest_sha256=None, cache_state="generated-chunks"),
                    identity={name: "same" for name in ("host", "native_extension_sha256", "files", "runner_sha256",
                                                        "contract_code_sha256", "generator_sha256", "input_reader_sha256")},
                    measurement=dict(controls={"input": "same", "result": "same"}, harness_seconds=2.,
                                     execution_seconds=1., peak_rss_bytes=1000., cpu_seconds=2.))
    candidate = copy.deepcopy(baseline)
    candidate["measurement"].update(harness_seconds=1., cpu_seconds=1.)
    candidate["identity"]["input_library_sha256"] = "fixed-input-helper"
    return [copy.deepcopy(dict(legacy=baseline, fused=candidate)) for _ in range(30)]


def test_eligible_harness_screen_is_not_production_qualification(comparison):
    result = comparison.compare(pairs(), "fused")
    assert result["retention_signal"]
    assert not result["performance_qualification"]
    assert result["metrics"]["harness_seconds"]["median_paired_reduction"] == .5
    assert not comparison.compare(pairs()[:3], "fused")["retention_signal"]


@pytest.mark.parametrize("field,value", [("harness_seconds", 2.), ("execution_seconds", 1.1),
                                        ("peak_rss_bytes", 1100.), ("cpu_seconds", 2.2)])
def test_fast_generation_cannot_hide_harness_or_resource_regression(comparison, field, value):
    rows = pairs()
    for row in rows:
        row["fused"]["measurement"][field] = value
    assert not comparison.compare(rows, "fused")["retention_signal"]


@pytest.mark.parametrize("change", ["failure", "workload", "output", "host", "cache", "library"])
def test_changed_evidence_cannot_form_a_pair(comparison, change):
    rows = pairs()
    candidate = rows[0]["fused"]
    if change == "failure":
        candidate["status"] = "failed"
    elif change == "workload":
        candidate["workload"]["ticks"] = 999
    elif change == "output":
        candidate["measurement"]["controls"]["result"] = "different"
    elif change == "host":
        candidate["identity"]["host"] = "different"
    elif change == "cache":
        candidate["source"]["cache_state"] = "cold"
    else:
        candidate["identity"]["input_library_sha256"] = "changed-within-campaign"
    with pytest.raises(ValueError):
        comparison.compare(rows, "fused")
