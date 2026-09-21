import importlib
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.native


@pytest.fixture
def handoff(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "benchmarks"))
    module = importlib.import_module("controlled_handoff")
    if module.TickRing is None:
        pytest.skip("native tick ring extension is not built")
    return module


@pytest.mark.parametrize("path", ["in_place", "copy", "lease", "direct"])
def test_full_factor_parity_across_many_small_ring_wraps(handoff, path):
    result = handoff.measure(handoff.make_ticks(4097), path=path, batch=16, capacity=32)
    assert result["snapshot"]["processed"] == 4097
    assert result["snapshot"]["sequence_errors"] == 0
    if path != "direct":
        assert result["metrics"]["pushed"] == result["metrics"]["popped"] == 4097


def test_scheduled_arrivals_include_stall_and_admission_backlog(handoff):
    result = handoff.measure(handoff.make_ticks(4096), batch=32, capacity=64,
                             rate=1_000_000, stall_seconds=0.03, probes=True)
    assert result["scheduled_to_completion_ns"]["max"] >= 20_000_000
    assert result["admission_lateness_ns"]["max"] >= 20_000_000
    assert result["metrics"]["dropped"] == 0


def test_thread_failure_reaches_caller(handoff, monkeypatch):
    original = handoff.TickRing

    class BrokenProducer:
        def __init__(self, capacity):
            self.ring = original(capacity)

        def __getattr__(self, name):
            return getattr(self.ring, name)

        def push_batch(self, records):
            raise RuntimeError("injected producer failure")

    monkeypatch.setattr(handoff, "TickRing", BrokenProducer)
    with pytest.raises(RuntimeError, match="injected producer failure"):
        handoff.measure(handoff.make_ticks(1024), timeout=1)


def test_stalled_threads_have_a_deadline(handoff):
    with pytest.raises(TimeoutError):
        handoff.measure(handoff.make_ticks(4096), batch=32, capacity=64,
                        stall_seconds=0.05, timeout=0.01)


@pytest.mark.parametrize("path", ["in_place", "copy", "lease"])
def test_bounded_diagnostics_preserve_complete_factors(handoff, path):
    records = handoff.make_ticks(8195)
    off = handoff.measure(records, path=path, batch=32, capacity=64)
    on = handoff.measure(records, path=path, batch=32, capacity=64, diagnostics=True)
    assert on["snapshot"] == off["snapshot"]
    assert off["diagnostics"] is None
    summary = on["diagnostics"]
    for name in ("producer_batches", "consumer_batches", "enqueue_to_completion"):
        assert summary[name]["records"] == len(records)
    assert summary["pending_records"] == 0
    assert summary["pending_high_water"] <= 96
    assert summary["sampled_high_water"] <= 64
    assert on["shutdown"]["status"] == "joined"
    assert not on["shutdown"]["live_threads"]


def test_health_signals_include_full_queue_and_original_schedule(handoff):
    result = handoff.measure(handoff.make_ticks(8192), batch=32, capacity=64,
                             rate=1_000_000, stall_seconds=.03, diagnostics=True)
    metrics = result["diagnostics"]
    assert metrics["full_wait_episodes"] > 0
    assert metrics["full_observations"] > 0
    assert metrics["sampled_high_water"] == 64
    assert metrics["scheduled_to_completion"]["maximum_ns"] >= 20_000_000
    assert metrics["admission_lateness"]["maximum_ns"] >= 20_000_000


def test_cancelled_pipeline_has_final_accounting_and_no_result(handoff):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError) as error:
        handoff.measure(handoff.make_ticks(4096), diagnostics=True, cancel=cancel)
    report = error.value.handoff_report
    assert report["status"] == "failed"
    assert report["accounting_final"]
    assert report["shutdown"]["status"] == "joined"
    assert report["unaccepted_records"] == 4096
    assert report["snapshot"] is None


def test_partial_publication_failure_reports_accepted_undrained_records(handoff, monkeypatch):
    original = handoff.TickRing

    class PartialProducer:
        def __init__(self, capacity):
            self.ring = original(capacity)

        def __getattr__(self, name):
            return getattr(self.ring, name)

        def push_batch(self, records):
            return self.ring.push_batch(records[:1])

    monkeypatch.setattr(handoff, "TickRing", PartialProducer)
    with pytest.raises(RuntimeError, match="partial acceptance") as error:
        handoff.measure(handoff.make_ticks(4096), diagnostics=True)
    report = error.value.handoff_report
    assert report["metrics"]["pushed"] == 1
    assert report["unaccepted_records"] == 4095
    assert report["accepted_uncompleted_records"] + report["completed_records"] == 1
    assert report["snapshot"] is None


@pytest.mark.parametrize("path", ["copy", "lease"])
def test_consumer_failure_releases_lease_and_never_returns_factors(handoff, monkeypatch, path):
    original = handoff.TickFactorProcessor
    instances = []

    class BrokenConsumer:
        def __init__(self):
            self.processor = original()
            instances.append(self)

        @property
        def snapshot(self):
            return self.processor.snapshot

        def process_batch(self, records):
            if self is instances[0]:
                return self.processor.process_batch(records)
            raise RuntimeError("injected factor failure")

    monkeypatch.setattr(handoff, "TickFactorProcessor", BrokenConsumer)
    with pytest.raises(RuntimeError, match="injected factor failure") as error:
        handoff.measure(handoff.make_ticks(4096), path=path, diagnostics=True)
    report = error.value.handoff_report
    assert report["failure"]["stage"] == "consume"
    assert report["completed_records"] == 0
    assert not report["metrics"]["active_lease"]
    assert report["accepted_uncompleted_records"] == report["metrics"]["pushed"]
    assert report["snapshot"] is None


def test_direct_failure_does_not_invent_queue_accounting(handoff, monkeypatch):
    original = handoff.TickFactorProcessor
    instances = []

    class BrokenDirect:
        def __init__(self):
            self.processor = original()
            self.calls = 0
            instances.append(self)

        @property
        def snapshot(self):
            return self.processor.snapshot

        def process_batch(self, records):
            self.calls += 1
            if self is instances[-1] and len(instances) > 1 and self.calls == 2:
                raise RuntimeError("direct failure")
            return self.processor.process_batch(records)

    monkeypatch.setattr(handoff, "TickFactorProcessor", BrokenDirect)
    with pytest.raises(RuntimeError, match="direct failure") as error:
        handoff.measure(handoff.make_ticks(4096), path="direct")
    report = error.value.handoff_report
    assert report["completed_records"] == 1024
    assert report["metrics"] is None
    assert report["unaccepted_records"] is report["accepted_uncompleted_records"] is None


def test_uncooperative_worker_is_reported_without_unbounded_join(handoff, monkeypatch):
    original = handoff.TickRing
    entered, release = threading.Event(), threading.Event()

    class StuckProducer:
        def __init__(self, capacity):
            self.ring = original(capacity)

        def __getattr__(self, name):
            return getattr(self.ring, name)

        def push_batch(self, records):
            entered.set()
            release.wait(2)
            return self.ring.push_batch(records)

    monkeypatch.setattr(handoff, "TickRing", StuckProducer)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError) as error:
            handoff.measure(handoff.make_ticks(4096), timeout=.1, cleanup_timeout=.01, diagnostics=True)
        assert entered.is_set()
        assert time.monotonic() - started < 1
        report = error.value.handoff_report
        assert report["shutdown"]["status"] == "incomplete"
        assert report["shutdown"]["process_isolation_required"]
        assert report["metrics"] is report["diagnostics"] is report["completed_records"] is None
        assert not report["accounting_final"]
    finally:
        release.set()


@pytest.mark.parametrize("option", [dict(timeout=float("nan")), dict(rate=float("inf")),
                                    dict(cleanup_timeout=-1), dict(stall_seconds=-1),
                                    dict(capacity=3), dict(batch=0)])
def test_nonfinite_or_unbounded_lifecycle_options_are_rejected(handoff, option):
    with pytest.raises(ValueError):
        handoff.measure(handoff.make_ticks(1024), **option)
