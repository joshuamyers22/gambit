import threading
import time

import numpy as np
import pytest

from gambit.factor_cache import TICK_DTYPE, TickRing

pytestmark = pytest.mark.native


def _ticks(start: int, count: int):
    ticks = np.zeros(count, dtype=TICK_DTYPE)
    ticks["sequence"] = np.arange(start, start + count)
    ticks["event_time_ns"] = ticks["sequence"] * 10
    ticks["receive_time_ns"] = ticks["event_time_ns"] + 2
    ticks["price"] = 100 + ticks["sequence"]
    ticks["quantity"] = 1
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
