from __future__ import annotations

from collections import defaultdict, deque

import numpy as np
import pytest
from gambit.compute_pnl import calc_trade_pnl

from gambit.factor_cache import TICK_DTYPE, MappedFloat64Column, TickFactorProcessor, TickRing


def _fifo_reference(open_lots, trades, multiplier):
    positions = deque((int(quantity), float(price)) for quantity, price in open_lots if quantity)
    realized = 0.0
    for quantity, price in trades:
        remaining = int(quantity)
        while remaining and positions and np.sign(positions[0][0]) != np.sign(remaining):
            open_quantity, open_price = positions.popleft()
            matched = min(abs(open_quantity), abs(remaining))
            realized += matched * np.sign(open_quantity) * (price - open_price) * multiplier
            open_quantity -= int(np.sign(open_quantity)) * matched
            remaining -= int(np.sign(remaining)) * matched
            if open_quantity:
                positions.appendleft((open_quantity, open_price))
        if remaining:
            positions.append((remaining, float(price)))
    return positions, realized


def test_cython_fifo_matches_reference_with_zero_quantity_events() -> None:
    rng = np.random.default_rng(20260831)
    for _ in range(200):
        open_lots = list(zip(rng.integers(-20, 21, 12), rng.uniform(10, 500, 12)))
        # Open lots supplied by account state are one-sided; zero lots remain valid no-ops.
        side = int(rng.choice((-1, 1)))
        open_lots = [(abs(int(quantity)) * side, float(price)) for quantity, price in open_lots]
        trades = list(zip(rng.integers(-30, 31, 30), rng.uniform(10, 500, 30)))
        multiplier = float(rng.choice((0.01, 1.0, 10.0, 50.0)))
        expected_positions, expected_realized = _fifo_reference(open_lots, trades, multiplier)

        quantities, prices, realized = calc_trade_pnl(
            np.asarray([item[0] for item in open_lots], dtype=int),
            np.asarray([item[1] for item in open_lots], dtype=float),
            np.asarray([item[0] for item in trades], dtype=int),
            np.asarray([item[1] for item in trades], dtype=float),
            multiplier,
        )

        assert quantities.tolist() == [item[0] for item in expected_positions]
        assert prices.tolist() == pytest.approx([item[1] for item in expected_positions])
        assert realized == pytest.approx(expected_realized)


def test_cython_fifo_rejects_misaligned_quantity_and_price_arrays() -> None:
    empty_int = np.array([], dtype=int)
    empty_float = np.array([], dtype=float)
    with pytest.raises(ValueError, match="open quantities and prices"):
        calc_trade_pnl(np.array([1], dtype=int), empty_float, empty_int, empty_float, 1.0)
    with pytest.raises(ValueError, match="new quantities and prices"):
        calc_trade_pnl(empty_int, empty_float, np.array([1], dtype=int), empty_float, 1.0)


def _tick_reference(records):
    last_prices = {}
    totals = defaultdict(float)
    sequence_errors = 0
    expected_sequence = None
    maximum_latency = 0
    for record in records:
        sequence = int(record["sequence"])
        if expected_sequence is not None and sequence != expected_sequence:
            sequence_errors += 1
        expected_sequence = sequence + 1
        price = float(record["price"])
        quantity = float(record["quantity"])
        instrument = int(record["instrument_id"])
        totals["quantity"] += quantity
        totals["notional"] += price * quantity
        totals["spread"] += float(record["ask"] - record["bid"])
        totals["mid"] += float((record["ask"] + record["bid"]) * 0.5)
        if instrument in last_prices and last_prices[instrument] != 0:
            totals["absolute_return"] += abs(price / last_prices[instrument] - 1.0)
        last_prices[instrument] = price
        maximum_latency = max(maximum_latency, int(record["receive_time_ns"] - record["event_time_ns"]))
    count = len(records)
    return {
        "processed": count,
        "sequence_errors": sequence_errors,
        "instrument_count": len(last_prices),
        "total_quantity": totals["quantity"],
        "total_notional": totals["notional"],
        "mean_spread": totals["spread"] / count if count else 0.0,
        "mean_mid": totals["mid"] / count if count else 0.0,
        "mean_absolute_return": totals["absolute_return"] / count if count else 0.0,
        "maximum_latency_ns": maximum_latency,
    }


@pytest.mark.native
def test_native_tick_processor_matches_multi_instrument_reference() -> None:
    if TickRing is None or TickFactorProcessor is None:
        pytest.skip("native factor cache extension is not built")
    rng = np.random.default_rng(20260831)
    records = np.zeros(500, dtype=TICK_DTYPE)
    records["sequence"] = np.arange(500)
    records["sequence"][::73] += 1
    records["event_time_ns"] = np.arange(500) * 1_000
    records["receive_time_ns"] = records["event_time_ns"] + rng.integers(0, 500, 500)
    records["price"] = rng.uniform(1, 5_000, 500)
    records["quantity"] = rng.uniform(0.01, 100, 500)
    spread = rng.uniform(0, 2, 500)
    records["bid"] = records["price"] - spread / 2
    records["ask"] = records["price"] + spread / 2
    records["instrument_id"] = rng.integers(0, 17, 500)

    ring = TickRing(512)
    processor = TickFactorProcessor()
    assert ring.push_batch(records) == len(records)
    while ring.depth:
        ring.process_batch(processor, int(rng.integers(1, 32)))

    expected = _tick_reference(records)
    actual = processor.snapshot
    for key in ("processed", "sequence_errors", "instrument_count", "maximum_latency_ns"):
        assert actual[key] == expected[key]
    for key in ("total_quantity", "total_notional", "mean_spread", "mean_mid", "mean_absolute_return"):
        assert actual[key] == pytest.approx(expected[key], rel=1e-12, abs=1e-12)


@pytest.mark.native
@pytest.mark.parametrize("factory_name", ["create", "create_chunked", "create_chunked_v3"])
def test_native_mapped_columns_match_numpy_reference_for_random_slices(tmp_path, factory_name) -> None:
    if MappedFloat64Column is None:
        pytest.skip("native factor cache extension is not built")
    rng = np.random.default_rng(20260831)
    values = rng.standard_normal(70_013).astype(np.float64)
    values[rng.choice(len(values), 100, replace=False)] = np.nan
    path = tmp_path / f"{factory_name}.bin"
    column = getattr(MappedFloat64Column, factory_name)(str(path), values)

    assert np.array_equal(column.values, values, equal_nan=True)
    for _ in range(100):
        start, stop = sorted(rng.integers(0, len(values) + 1, 2).tolist())
        assert np.array_equal(column.slice(start, stop), values[start:stop], equal_nan=True)
