import numpy as np
import pytest

from gambit.tick_backtest import BOOK_DTYPE, TopOfBookBacktester

pytestmark = [pytest.mark.native, pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")]


def make_books(count, instruments=1):
    rows = np.zeros(count, dtype=BOOK_DTYPE)
    rows["sequence"] = np.arange(count)
    rows["event_time_ns"] = np.arange(count) * 10
    rows["receive_time_ns"] = rows["event_time_ns"] + 1
    rows["bid"], rows["ask"] = 99, 101
    rows["bid_size"], rows["ask_size"] = 2, 2
    rows["instrument_id"] = np.arange(count) % instruments
    return rows


def python_reference(rows, *, instruments, cash, target_lots, rebalance_events, fee_ppm=0, latency_ns=0):
    """Independent event-driven oracle; Python integers are unbounded."""
    initial_cash = cash
    positions = [0] * instruments
    marks = [0] * instruments
    observed = [0] * instruments
    active = {}
    orders, fills = [], []
    total_fees = 0
    for row in rows:
        instrument = int(row["instrument_id"])
        seq, timestamp = int(row["sequence"]), int(row["receive_time_ns"])
        marks[instrument] = int(row["bid"])
        if instrument in active:
            order = active[instrument]
            if timestamp - order["timestamp_ns"] >= latency_ns:
                buy = order["remaining"] > 0
                size = min(abs(order["remaining"]), int(row["ask_size" if buy else "bid_size"]))
                price = int(row["ask" if buy else "bid"])
                notional = size * price
                fee = (notional * fee_ppm + 999999) // 1000000
                if size and buy and notional + fee > cash:
                    order["status"] = 3
                    del active[instrument]
                elif size:
                    quantity = size if buy else -size
                    cash -= quantity * price + fee
                    total_fees += fee
                    positions[instrument] += quantity
                    order["remaining"] -= quantity
                    fills.append((order["id"], seq, timestamp, quantity, price, fee, instrument, 0))
                    if not order["remaining"]:
                        order["status"] = 1
                        del active[instrument]
        observed[instrument] += 1
        if observed[instrument] % rebalance_events == 0:
            if instrument in active:
                active.pop(instrument)["status"] = 2
            target = target_lots if (observed[instrument] // rebalance_events) % 2 else 0
            quantity = target - positions[instrument]
            if quantity:
                order = dict(id=len(orders) + 1, sequence=seq, timestamp_ns=timestamp, quantity=quantity,
                             remaining=quantity, instrument_id=instrument, status=0)
                orders.append(order)
                active[instrument] = order
    equity = cash + sum(position * mark for position, mark in zip(positions, marks))
    return dict(processed=len(rows), cash=cash, equity=equity, net_pnl=equity - initial_cash,
                total_fees=total_fees, positions=positions,
                orders=[tuple(order.values()) for order in orders], fills=fills)


def assert_matches_reference(actual, expected):
    for name in ("processed", "cash", "equity", "net_pnl", "total_fees"):
        assert actual[name] == expected[name]
    np.testing.assert_array_equal(actual["positions"], expected["positions"])
    assert actual["orders"].tolist() == expected["orders"]
    assert actual["fills"].tolist() == expected["fills"]


@pytest.mark.parametrize("chunk_size", [1, 17, 257])
@pytest.mark.parametrize("latency", [0, 31])
def test_multinstrument_order_fill_and_pnl_parity(chunk_size, latency):
    rows = make_books(1003, instruments=8)
    rng = np.random.default_rng(20260904)
    rows["bid"] = rng.integers(90, 111, len(rows))
    rows["ask"] = rows["bid"] + rng.integers(0, 5, len(rows))
    rows["bid_size"] = rng.integers(0, 8, len(rows))
    rows["ask_size"] = rng.integers(0, 8, len(rows))
    rows.setflags(write=False)
    config = dict(instruments=8, cash=1000, target_lots=9, rebalance_events=7, fee_ppm=1500, latency_ns=latency)
    engine = TopOfBookBacktester(**config)
    for offset in range(0, len(rows), chunk_size):
        assert engine.process_batch(rows[offset:offset + chunk_size]) == len(rows[offset:offset + chunk_size])
    actual = engine.result()
    assert_matches_reference(actual, python_reference(rows, **config))
    assert any(actual["orders"]["status"] == 3)  # shared cash causes real risk rejections
    assert all(fill["sequence"] > actual["orders"][int(fill["order_id"]) - 1]["sequence"] for fill in actual["fills"])
    assert not actual["orders"].flags.writeable


def test_manual_partial_fill_fee_and_terminal_mark():
    engine = TopOfBookBacktester(1, 1000, 3, 2, fee_ppm=10000)
    rows = make_books(4)
    engine.process_batch(rows)
    out = engine.result()
    assert out["fills"]["sequence"].tolist() == [2, 3]
    assert out["fills"]["quantity"].tolist() == [2, 1]
    assert out["total_fees"] == 5
    assert out["cash"] == 692
    assert out["equity"] == 989
    assert out["net_pnl"] == -11
    assert out["orders"][-1]["remaining"] == -3  # no invented terminal fill


@pytest.mark.parametrize("field,value", [("sequence", 7), ("receive_time_ns", -1), ("bid", 0),
                                         ("ask", 98), ("ask_size", -1), ("instrument_id", 1), ("flags", 1)])
def test_invalid_events_fail_closed(field, value):
    rows = make_books(4)
    rows[field][2] = value
    engine = TopOfBookBacktester(1, 1000, 3, 2)
    with pytest.raises(ValueError):
        engine.process_batch(rows)
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()
    with pytest.raises(RuntimeError, match="failed"):
        engine.process_batch(make_books(1))


def test_capacity_and_arithmetic_overflow_cannot_publish_results():
    engine = TopOfBookBacktester(1, 1000, 3, 1, audit_capacity=1)
    with pytest.raises(RuntimeError, match="capacity"):
        engine.process_batch(make_books(4))
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()
    engine = TopOfBookBacktester(1, 1000, 3, 1)
    rows = make_books(3)
    rows["ask"] = np.iinfo(np.int64).max
    with pytest.raises(OverflowError):
        engine.process_batch(rows)
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()


def test_batch_shape_dtype_and_alignment_are_explicit():
    engine = TopOfBookBacktester(1, 1000, 3, 2)
    with pytest.raises(ValueError):
        engine.process_batch(make_books(4).reshape(2, 2))
    with pytest.raises(TypeError):
        engine.process_batch(make_books(4)[::2])
    with pytest.raises(TypeError):
        engine.process_batch(np.zeros(3))
    unaligned = np.ndarray(1, dtype=BOOK_DTYPE, buffer=bytearray(65), offset=1)
    with pytest.raises(ValueError, match="aligned"):
        engine.process_batch(unaligned)
    assert engine.result()["processed"] == 0


def test_stale_quote_and_terminal_valuation_overflow_fail_closed():
    engine = TopOfBookBacktester(1, 1000, 1, 2, maximum_feed_age_ns=0)
    with pytest.raises(ValueError):
        engine.process_batch(make_books(1))
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()
    engine = TopOfBookBacktester(1, int(np.iinfo(np.int64).max), 1, 2)
    rows = make_books(4)
    rows["bid"], rows["ask"] = 1, 1
    rows["bid"][-1], rows["ask"][-1] = 3, 3
    engine.process_batch(rows)
    with pytest.raises(OverflowError):
        engine.result()
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()


@pytest.mark.parametrize("fee_ppm", [0, 1, 1000000])
def test_fee_boundaries_and_immutable_result_snapshots(fee_ppm):
    config = dict(instruments=1, cash=1000, target_lots=3, rebalance_events=2, fee_ppm=fee_ppm)
    rows = make_books(12)
    engine = TopOfBookBacktester(**config)
    engine.process_batch(rows[:2])
    old = engine.result()
    engine.process_batch(rows[2:])
    assert old["positions"].tolist() == [0]
    assert old["orders"][0]["remaining"] == 3
    assert_matches_reference(engine.result(), python_reference(rows, **config))


@pytest.mark.parametrize("kwargs", [{"instruments": 0}, {"instruments": 4097}, {"cash": -1},
                                    {"target_lots": 0}, {"rebalance_events": 0}, {"fee_ppm": 1000001},
                                    {"latency_ns": -1}, {"audit_capacity": 0}, {"maximum_feed_age_ns": -1}])
def test_invalid_backtest_configuration_is_rejected(kwargs):
    config = dict(instruments=1, cash=1000, target_lots=3, rebalance_events=2)
    config.update(kwargs)
    with pytest.raises(ValueError):
        TopOfBookBacktester(**config)
