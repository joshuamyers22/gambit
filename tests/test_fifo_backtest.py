"""Independent conservative exchange-queue oracle and adversarial fixtures."""

import numpy as np
import pytest

from gambit.tick_backtest import QUEUE_DTYPE, TopOfBookBacktester
from test_top_of_book_backtest import assert_matches_reference, make_books

pytestmark = [pytest.mark.native, pytest.mark.skipif(TopOfBookBacktester is None, reason="native extension required")]


def make_events(count, instruments=1):
    events = np.zeros(count, dtype=QUEUE_DTYPE)
    events["book"] = make_books(count, instruments)
    return events


def python_fifo(events, *, instruments, cash, target_lots, rebalance_events, fee_ppm=0, latency_ns=0):
    """Python integers; no native calls or shared queue/fee implementation."""
    initial_cash = cash
    positions, marks, counts = [0] * instruments, [0] * instruments, [0] * instruments
    active = {}
    orders, queues, fills = [], [], []
    fees = 0
    for event in events:
        book = {name: int(event["book"][name]) for name in event["book"].dtype.names}
        inst, seq, now = book["instrument_id"], book["sequence"], book["receive_time_ns"]
        marks[inst] = book["bid"]
        if inst in active:
            index = active[inst]
            order, queue = orders[index], queues[index]
            buy = order["remaining"] > 0
            if queue["arrival_time_ns"] == -1:
                if now >= order["timestamp_ns"] + latency_ns:
                    same_side = book["bid" if buy else "ask"]
                    crosses = queue["limit_price"] >= book["ask"] if buy else queue["limit_price"] <= book["bid"]
                    if queue["limit_price"] != same_side or crosses:
                        order["status"] = 4
                        del active[inst]
                    else:
                        queue.update(ahead=book["bid_size" if buy else "ask_size"],
                                     initial_ahead=book["bid_size" if buy else "ask_size"],
                                     arrival_sequence=seq, arrival_time_ns=now)
            elif int(event["trade_price"]) == queue["limit_price"] and int(event["aggressor"]) == (-1 if buy else 1):
                volume = int(event["trade_size"])
                remaining_volume = max(0, volume - queue["ahead"])
                queue["ahead"] = max(0, queue["ahead"] - volume)
                amount = min(abs(order["remaining"]), remaining_volume)
                if amount:
                    price = queue["limit_price"]
                    fee = (amount * price * fee_ppm + 999999) // 1000000
                    if buy and amount * price + fee > cash:
                        order["status"] = 3
                        del active[inst]
                    else:
                        signed = amount if buy else -amount
                        cash -= signed * price + fee
                        positions[inst] += signed
                        fees += fee
                        order["remaining"] -= signed
                        fills.append((index + 1, seq, now, signed, price, fee, inst, 0))
                        if order["remaining"] == 0:
                            order["status"] = 1
                            del active[inst]
        counts[inst] += 1
        if counts[inst] % rebalance_events == 0:
            if inst in active:
                orders[active.pop(inst)]["status"] = 2
            target = target_lots if (counts[inst] // rebalance_events) % 2 else 0
            quantity = target - positions[inst]
            if quantity:
                active[inst] = len(orders)
                orders.append(dict(id=len(orders) + 1, sequence=seq, timestamp_ns=now, quantity=quantity,
                                   remaining=quantity, instrument_id=inst, status=0))
                queues.append(dict(limit_price=book["bid" if quantity > 0 else "ask"], ahead=0,
                                   initial_ahead=0, arrival_sequence=2**64 - 1, arrival_time_ns=-1))
    equity = cash + sum(p * m for p, m in zip(positions, marks))
    return dict(processed=len(events), cash=cash, equity=equity, net_pnl=equity - initial_cash,
                total_fees=fees, positions=positions, orders=[tuple(o.values()) for o in orders],
                fills=fills, queues=[tuple(q.values()) for q in queues])


def check(events, config, chunk=17):
    engine = TopOfBookBacktester(**config, execution_model="fifo")
    for offset in range(0, len(events), chunk):
        assert engine.process_queue_batch(events[offset:offset + chunk]) == len(events[offset:offset + chunk])
    result = engine.result()
    expected = python_fifo(events, **config)
    assert_matches_reference(result, expected)
    assert result["queues"].tolist() == expected["queues"]
    for fill in result["fills"]:
        queue = result["queues"][int(fill["order_id"]) - 1]
        assert fill["sequence"] > queue["arrival_sequence"]
        assert fill["price"] == queue["limit_price"]
    return result


def test_manual_arrival_cancellation_ignored_partial_fifo_fills():
    events = make_events(17)
    events["book"]["bid_size"] = 5
    # Submit at 7, arrive after trade at 8, ignoring that trade's 100 lots.
    events["aggressor"][[8, 10, 11, 12, 13]] = -1
    events["trade_price"][[8, 10, 11, 12, 13]] = 99
    events["trade_size"][[8, 10, 11, 12, 13]] = [100, 3, 2, 2, 1]
    events["book"]["bid_size"][9:] = 0  # cancellations do not advance us
    result = check(events, dict(instruments=1, cash=1000, target_lots=3, rebalance_events=8, fee_ppm=10000))
    assert result["fills"]["sequence"].tolist() == [12, 13]
    assert result["fills"]["quantity"].tolist() == [2, 1]
    assert result["queues"][0].tolist() == (99, 0, 5, 8, 81)
    assert result["cash"] == 700
    assert result["total_fees"] == 3
    assert result["orders"][1]["remaining"] == -3


@pytest.mark.parametrize("chunk", [1, 19, 257])
@pytest.mark.parametrize("latency", [0, 80, 10000])
@pytest.mark.parametrize("cash", [100, 1000000])
def test_seeded_queue_trace_parity(chunk, latency, cash):
    events = make_events(2003, 8)
    rng = np.random.default_rng(20260904)
    events["book"]["bid"] += np.arange(len(events)) // 200 % 3
    events["book"]["ask"] = events["book"]["bid"] + 2
    events["book"]["bid_size"] = rng.integers(0, 10, len(events))
    events["book"]["ask_size"] = rng.integers(0, 10, len(events))
    events["aggressor"] = rng.choice([-1, 1], len(events))
    events["trade_size"] = rng.integers(1, 20, len(events))
    events["trade_price"] = np.where(events["aggressor"] == -1, events["book"]["bid"], events["book"]["ask"])
    events.setflags(write=False)
    check(events, dict(instruments=8, cash=cash, target_lots=11, rebalance_events=13,
                       latency_ns=latency, fee_ppm=1500), chunk)


def test_equal_timestamps_obey_sequence_and_opposing_price_only():
    events = make_events(12)
    events["book"]["event_time_ns"] = 0
    events["book"]["receive_time_ns"] = 1
    events["book"]["bid_size"] = 0
    events["trade_size"][5:9] = 100
    events["trade_price"][5:9] = [99, 98, 99, 99]
    events["aggressor"][5:9] = [-1, -1, 1, -1]
    result = check(events, dict(instruments=1, cash=1000, target_lots=3, rebalance_events=5))
    assert result["fills"]["sequence"].tolist() == [8]


@pytest.mark.parametrize("arrival_bid,arrival_ask", [(98, 101), (100, 101), (99, 99)])
def test_nonbest_and_crossing_arrivals_reject(arrival_bid, arrival_ask):
    events = make_events(4)
    events["book"]["bid"][2] = arrival_bid
    events["book"]["ask"][2] = arrival_ask
    result = check(events, dict(instruments=1, cash=1000, target_lots=3, rebalance_events=2))
    assert result["orders"][0]["status"] == 4
    assert len(result["fills"]) == 0


@pytest.mark.parametrize("field,value", [("trade_size", -1), ("trade_price", 99),
                                         ("aggressor", 1), ("reserved", 1)])
def test_bad_trade_records_fail_closed(field, value):
    events = make_events(4)
    events[field][2] = value
    engine = TopOfBookBacktester(1, 1000, 3, 2, execution_model="fifo")
    with pytest.raises(ValueError):
        engine.process_queue_batch(events)
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()
    with pytest.raises(RuntimeError, match="failed"):
        engine.process_queue_batch(events[:0])


def test_api_mode_layout_and_snapshot_isolation():
    with pytest.raises(ValueError):
        TopOfBookBacktester(1, 1000, 3, 2, execution_model="unknown")
    engine = TopOfBookBacktester(1, 1000, 3, 2, execution_model="fifo")
    with pytest.raises(ValueError):
        engine.process_batch(make_books(3))
    with pytest.raises(ValueError):
        TopOfBookBacktester(1, 1000, 3, 2).process_queue_batch(make_events(3))
    with pytest.raises(TypeError):
        engine.process_queue_batch(make_events(4)[::2])
    with pytest.raises(ValueError):
        engine.process_queue_batch(make_events(4).reshape(2, 2))
    with pytest.raises(ValueError, match="aligned"):
        engine.process_queue_batch(np.ndarray(1, dtype=QUEUE_DTYPE, buffer=bytearray(89), offset=1))
    events = make_events(4)
    engine.process_queue_batch(events[:2])
    old = engine.result()
    engine.process_queue_batch(events[2:])
    assert old["queues"][0]["arrival_time_ns"] == -1
    assert not old["queues"].flags.writeable


def test_queue_capacity_and_notional_overflow_fail_closed():
    events = make_events(10)
    for capacity in [1, 2]:
        engine = TopOfBookBacktester(1, 1000, 3, 1, audit_capacity=capacity, execution_model="fifo")
        with pytest.raises(RuntimeError, match="capacity"):
            engine.process_queue_batch(events)
        with pytest.raises(RuntimeError, match="failed"):
            engine.result()
    events["book"]["bid"] = 2**62
    events["book"]["ask"] = 2**62 + 1
    events["book"]["bid_size"] = 0
    events["trade_price"] = 2**62
    events["trade_size"] = 3
    events["aggressor"] = -1
    engine = TopOfBookBacktester(1, 2**63 - 1, 3, 2, execution_model="fifo")
    with pytest.raises(OverflowError):
        engine.process_queue_batch(events)
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()


@pytest.mark.parametrize("interval", [16, 10000])
def test_benchmark_generator_exact_python_trace(interval):
    from test_top_of_book_benchmark import load_benchmark

    benchmark = load_benchmark()
    events = benchmark.make_queue_events(0, 100003)
    result = check(events, dict(instruments=8, cash=10**13, target_lots=100,
                               rebalance_events=interval, fee_ppm=100, latency_ns=1_000_000), 4093)
    assert len(result["fills"]) > 0
    if interval == 16:
        assert any(result["fills"]["quantity"] < 0)


def test_shared_cash_priority_is_global_sequence_not_instrument_id():
    events = make_events(14, 2)
    events["book"]["instrument_id"] = 1 - events["book"]["instrument_id"]
    events["book"]["bid_size"] = 0
    events["aggressor"] = -1
    events["trade_price"] = 99
    events["trade_size"] = 1
    result = check(events, dict(instruments=2, cash=99, target_lots=1, rebalance_events=3))
    assert result["fills"]["instrument_id"].tolist() == [1]
    assert result["orders"][1]["status"] == 3


def test_resting_price_and_queue_survive_top_changes_and_additions():
    events = make_events(10)
    events["book"]["bid_size"] = 2
    events["book"]["bid_size"][5:] = 1000  # later additions are behind us
    events["book"]["bid"][5:] = 100  # resting limit remains at 99
    events["trade_price"][[6, 7]] = 99
    events["trade_size"][[6, 7]] = [2, 3]
    events["aggressor"][[6, 7]] = -1
    result = check(events, dict(instruments=1, cash=1000, target_lots=3, rebalance_events=4))
    assert result["fills"]["sequence"].tolist() == [7]
    assert result["fills"]["price"].tolist() == [99]


@pytest.mark.parametrize("fee_ppm", [0, 1, 1000000])
def test_manual_sell_queue_and_fee_boundaries(fee_ppm):
    events = make_events(17)
    events["book"]["bid_size"] = 0
    events["book"]["ask_size"] = 4
    events["trade_price"][[5, 9, 10, 11]] = [99, 101, 101, 101]
    events["aggressor"][[5, 9, 10, 11]] = [-1, 1, 1, 1]
    events["trade_size"][[5, 9, 10, 11]] = [3, 4, 2, 1]
    result = check(events, dict(instruments=1, cash=10000, target_lots=3, rebalance_events=4, fee_ppm=fee_ppm))
    assert result["fills"]["quantity"].tolist() == [3, -2, -1]
    assert result["queues"][1]["initial_ahead"] == 4
    assert result["fills"]["sequence"].tolist() == [5, 10, 11]


def test_fill_capacity_exhaustion_is_not_silent_truncation():
    events = make_events(9)
    events["book"]["bid_size"] = 0
    events["trade_price"] = 99
    events["trade_size"] = 1
    events["aggressor"] = -1
    engine = TopOfBookBacktester(1, 10000, 10, 4, audit_capacity=2, execution_model="fifo")
    with pytest.raises(RuntimeError, match="fill audit capacity"):
        engine.process_queue_batch(events)
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()


@pytest.mark.parametrize("price,side", [(0, 1), (99, 0), (99, 2)])
def test_positive_trade_requires_valid_price_and_aggressor(price, side):
    events = make_events(1)
    events["trade_size"], events["trade_price"], events["aggressor"] = 1, price, side
    engine = TopOfBookBacktester(1, 1000, 3, 2, execution_model="fifo")
    with pytest.raises(ValueError, match="trade event"):
        engine.process_queue_batch(events)
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()


def test_cancelled_order_reentry_joins_fresh_queue():
    events = make_events(14)
    events["book"]["bid_size"][12:] = 7
    result = check(events, dict(instruments=1, cash=1000, target_lots=3, rebalance_events=4))
    assert result["orders"]["status"].tolist() == [2, 0]
    assert result["queues"]["initial_ahead"].tolist() == [2, 7]
    assert result["queues"]["arrival_sequence"].tolist() == [4, 12]
    assert len(result["fills"]) == 0


@pytest.mark.parametrize("field,value", [("sequence", 9), ("receive_time_ns", -1), ("bid_size", -1),
                                         ("instrument_id", 2), ("flags", 1)])
def test_queue_mode_inherits_book_validation(field, value):
    events = make_events(4)
    events["book"][field][2] = value
    engine = TopOfBookBacktester(1, 1000, 3, 2, execution_model="fifo")
    with pytest.raises(ValueError):
        engine.process_queue_batch(events)
    with pytest.raises(RuntimeError, match="failed"):
        engine.result()
