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
