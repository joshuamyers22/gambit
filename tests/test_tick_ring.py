import gc
import threading
import time

import numpy as np
import pytest

from gambit.factor_cache import TICK_DTYPE, TickFactorProcessor, TickRing

pytestmark = pytest.mark.native


def _ticks(start: int, count: int):
    ticks = np.zeros(count, dtype=TICK_DTYPE)
    ticks["sequence"] = np.arange(start, start + count)
    ticks["event_time_ns"] = ticks["sequence"] * 10
    ticks["receive_time_ns"] = ticks["event_time_ns"] + 2
    ticks["price"] = 100 + ticks["sequence"]
    ticks["quantity"] = 1
    ticks["bid"] = ticks["price"] - 0.01
    ticks["ask"] = ticks["price"] + 0.01
    ticks["instrument_id"] = 7
    return ticks


def test_tick_record_is_one_cache_line() -> None:
    assert TICK_DTYPE.itemsize == 64


def test_ring_requires_power_of_two_capacity() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    with pytest.raises(ValueError, match="power of two"):
        TickRing(3)


def test_ring_preserves_sequence_across_wraparound() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(4)
    assert ring.push_batch(_ticks(0, 4)) == 4
    assert ring.pop_batch(2)["sequence"].tolist() == [0, 1]
    assert ring.push_batch(_ticks(4, 2)) == 2
    assert ring.pop_batch(4)["sequence"].tolist() == [2, 3, 4, 5]
    assert ring.depth == 0


def test_ring_rejects_newest_records_when_full() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(2)

    assert ring.push_batch(_ticks(0, 3)) == 2

    assert ring.metrics["dropped"] == 1
    assert ring.pop_batch(2)["sequence"].tolist() == [0, 1]


def test_zero_copy_lease_is_read_only_and_defers_release_until_view_dies() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(4)
    ring.push_batch(_ticks(0, 3))
    lease = ring.lease_batch(3)
    view = lease.values
    second_view = lease.values

    assert view["sequence"].tolist() == [0, 1, 2]
    assert view.flags.writeable is False
    with pytest.raises(ValueError, match="read-only"):
        view["sequence"][0] = 99
    lease.close()

    assert lease.closed is True
    assert ring.depth == 3
    assert ring.metrics["active_lease"] is True
    with pytest.raises(RuntimeError, match="closed"):
        lease.values

    del view
    gc.collect()

    assert ring.depth == 3
    del second_view
    gc.collect()

    assert ring.depth == 0
    assert ring.metrics["active_lease"] is False
    assert ring.metrics["popped"] == 3


def test_zero_copy_lease_stops_at_wrap_boundary() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(4)
    ring.push_batch(_ticks(0, 4))
    assert ring.pop_batch(3)["sequence"].tolist() == [0, 1, 2]
    ring.push_batch(_ticks(4, 3))

    with ring.lease_batch(4) as first:
        assert first.values["sequence"].tolist() == [3]
    with ring.lease_batch(4) as second:
        assert second.values["sequence"].tolist() == [4, 5, 6]

    assert ring.depth == 0


def test_zero_copy_lease_blocks_other_consumers_and_survives_ring_reference() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(4)
    ring.push_batch(_ticks(10, 2))
    lease = ring.lease_batch(2)
    view = lease.values

    with pytest.raises(RuntimeError, match="already active"):
        ring.lease_batch(1)
    with pytest.raises(RuntimeError, match="already active"):
        ring.pop_batch(1)

    del ring
    gc.collect()
    assert view["sequence"].tolist() == [10, 11]

    lease.close()
    del view
    del lease
    gc.collect()


def test_zero_copy_context_exception_keeps_retained_view_pinned() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(4)
    ring.push_batch(_ticks(0, 1))
    retained = None

    with pytest.raises(RuntimeError, match="research failure"):
        with ring.lease_batch(1) as lease:
            retained = lease.values
            raise RuntimeError("research failure")

    assert ring.depth == 1
    del retained
    gc.collect()
    assert ring.depth == 0


def test_wait_releases_gil_then_parks_until_producer_arrives() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(8)

    def produce():
        time.sleep(0.01)
        ring.push_batch(_ticks(42, 1))

    producer = threading.Thread(target=produce)
    producer.start()
    result = ring.wait_pop_batch(1, spin_count=1, timeout_seconds=0.2)
    producer.join()

    assert result["sequence"].tolist() == [42]
    assert ring.metrics["parks"] == 1
    assert ring.metrics["wakeups"] == 1


def test_wait_uses_bounded_adaptive_backoff() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(8)

    result = ring.wait_pop_batch(
        1,
        spin_count=64,
        backoff_count=3,
        maximum_backoff_seconds=0.0001,
        timeout_seconds=0,
    )

    assert len(result) == 0
    assert ring.metrics["spins"] == 64
    assert ring.metrics["yields"] == 1
    assert ring.metrics["backoffs"] == 3
    assert ring.metrics["parks"] == 0


def test_close_cancels_parked_consumer_without_timeout_polling() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(8)
    completed = threading.Event()

    def consume() -> None:
        assert len(ring.wait_pop_batch(1, spin_count=0, timeout_seconds=60)) == 0
        completed.set()

    consumer = threading.Thread(target=consume)
    consumer.start()
    time.sleep(0.01)
    ring.close()
    consumer.join(timeout=1)

    assert completed.is_set()
    assert ring.metrics["closed"] is True
    assert ring.metrics["wakeups"] == 1
    assert ring.metrics["park_timeouts"] == 0
    assert ring.push_batch(_ticks(0, 1)) == 0


def test_notifications_do_not_lose_racing_producer_wakeups() -> None:
    if TickRing is None:
        pytest.skip("native factor cache extension is not built")
    for sequence in range(100):
        ring = TickRing(2)
        received = []

        def consume() -> None:
            received.extend(
                ring.wait_pop_batch(1, spin_count=0, timeout_seconds=0.5)["sequence"].tolist()
            )

        consumer = threading.Thread(target=consume)
        consumer.start()
        ring.push_batch(_ticks(sequence, 1))
        consumer.join(timeout=1)

        assert received == [sequence]


def test_processor_consumes_ring_slots_in_place() -> None:
    if TickRing is None or TickFactorProcessor is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(8)
    processor = TickFactorProcessor()
    ring.push_batch(_ticks(10, 4))

    processed = ring.process_batch(processor, 4)
    snapshot = processor.snapshot

    assert processed == 4
    assert ring.depth == 0
    assert snapshot["processed"] == 4
    assert snapshot["sequence_errors"] == 0
    assert snapshot["total_quantity"] == 4
    assert snapshot["total_notional"] == pytest.approx(446.0)
    assert snapshot["mean_spread"] == pytest.approx(0.02)
    assert snapshot["mean_mid"] == pytest.approx(111.5)
    assert snapshot["maximum_latency_ns"] == 2
    expected_mean_absolute_return = (111 / 110 - 1 + 112 / 111 - 1 + 113 / 112 - 1) / 4
    assert snapshot["mean_absolute_return"] == pytest.approx(expected_mean_absolute_return)


def test_processor_detects_sequence_gaps() -> None:
    if TickRing is None or TickFactorProcessor is None:
        pytest.skip("native factor cache extension is not built")
    ring = TickRing(4)
    processor = TickFactorProcessor()
    ticks = _ticks(0, 2)
    ticks["sequence"][1] = 3
    ring.push_batch(ticks)

    ring.process_batch(processor, 2)

    assert processor.snapshot["sequence_errors"] == 1
