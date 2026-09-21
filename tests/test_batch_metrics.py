import importlib
from pathlib import Path

import pytest


@pytest.fixture
def metrics(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "benchmarks"))
    return importlib.import_module("batch_metrics")


def test_histogram_quantiles_are_bounded_and_records_are_separate(metrics):
    histogram = metrics.Histogram()
    assert histogram.snapshot()["p99_upper_bound_ns"] is None
    for value in (0, 1, 2, 3, 4, 2**63):
        histogram.add(value, 100)
    result = histogram.snapshot()
    assert result["count"] == 6 and result["records"] == 600
    assert result["p50_upper_bound_ns"] == 3
    assert result["maximum_ns"] == 2**63
    assert result["p99_upper_bound_ns"] == 2**64 - 1
    assert len(result["buckets"]) == 65
    with pytest.raises(ValueError):
        histogram.add(-1)
    with pytest.raises(ValueError):
        histogram.add(2**64)


def test_split_batch_age_and_schedule_include_backlog(metrics):
    monitor = metrics.HandoffMetrics(8, 4)
    monitor.reserve(4, 100, 20)
    monitor.reserve(3, 110, 30)
    monitor.complete(2, 150)
    monitor.complete(5, 200)
    result = monitor.snapshot()
    assert result["enqueue_to_completion"]["records"] == 7
    assert result["enqueue_to_completion"]["count"] == 3
    assert result["enqueue_to_completion"]["maximum_ns"] == 100
    assert result["scheduled_to_completion"]["maximum_ns"] == 180
    assert result["admission_lateness"]["maximum_ns"] == 80
    assert result["pending_records"] == result["pending_intervals"] == 0


def test_sidecar_capacity_exhaustion_is_explicit_and_never_evicts(metrics):
    monitor = metrics.HandoffMetrics(4, 2)
    for i in range(6):
        monitor.reserve(1, i, None)
    with pytest.raises(RuntimeError, match="capacity"):
        monitor.reserve(1, 6, None)
    monitor.complete(6, 10)
    assert monitor.snapshot()["enqueue_to_completion"]["records"] == 6
    with pytest.raises(RuntimeError, match="timestamp"):
        monitor.complete(1, 11)


def test_summary_storage_does_not_grow_with_completed_volume(metrics):
    monitor = metrics.HandoffMetrics(8, 4)
    for i in range(10000):
        monitor.reserve(4, i, None)
        monitor.complete(4, i + 1)
    assert not monitor.pending
    assert monitor.pending_high_water == 4
    assert len(monitor.age.buckets) == 65
    assert monitor.age.records == 40000


@pytest.mark.parametrize("count", [0, -1, True, 1.5])
def test_invalid_reservation_cannot_bypass_metadata_bound(metrics, count):
    monitor = metrics.HandoffMetrics(4, 2)
    with pytest.raises(ValueError):
        monitor.reserve(count, 10, None)
    assert not monitor.pending
    assert monitor.pending_records == 0
