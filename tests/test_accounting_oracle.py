from __future__ import annotations

from collections import deque

import numpy as np
from gambit.compute_pnl import calc_trade_pnl


def fifo_ledger_oracle(
    open_lots: list[tuple[int, float]], trades: list[tuple[int, float]], multiplier: float
) -> tuple[np.ndarray, np.ndarray, float]:
    """Small, independent FIFO ledger used only to verify the native engine."""
    positions = deque((qty, price) for qty, price in open_lots if qty)
    realized = 0.0

    for trade_qty, trade_price in trades:
        remaining = trade_qty
        while remaining and positions and np.sign(positions[0][0]) != np.sign(remaining):
            position_qty, position_price = positions.popleft()
            matched = min(abs(position_qty), abs(remaining))
            realized += matched * np.sign(position_qty) * (trade_price - position_price) * multiplier

            position_qty -= int(np.sign(position_qty)) * matched
            remaining -= int(np.sign(remaining)) * matched
            if position_qty:
                positions.appendleft((position_qty, position_price))

        if remaining:
            positions.append((remaining, trade_price))

    quantities = np.asarray([qty for qty, _ in positions], dtype=int)
    prices = np.asarray([price for _, price in positions], dtype=float)
    return quantities, prices, realized


def test_native_pnl_matches_independent_fifo_oracle() -> None:
    rng = np.random.default_rng(20260827)

    for _ in range(250):
        initial_side = int(rng.choice([-1, 1]))
        open_lots = [
            (initial_side * int(rng.integers(1, 20)), float(rng.uniform(25, 250)))
            for _ in range(int(rng.integers(0, 8)))
        ]
        trades = [
            (int(rng.choice([-1, 1])) * int(rng.integers(1, 25)), float(rng.uniform(25, 250)))
            for _ in range(int(rng.integers(1, 30)))
        ]
        multiplier = float(rng.choice([1, 10, 50, 100]))

        expected_qtys, expected_prices, expected_realized = fifo_ledger_oracle(open_lots, trades, multiplier)
        actual_qtys, actual_prices, actual_realized = calc_trade_pnl(
            np.asarray([qty for qty, _ in open_lots], dtype=int),
            np.asarray([price for _, price in open_lots], dtype=float),
            np.asarray([qty for qty, _ in trades], dtype=int),
            np.asarray([price for _, price in trades], dtype=float),
            multiplier,
        )

        assert np.array_equal(actual_qtys, expected_qtys)
        assert np.allclose(actual_prices, expected_prices)
        assert np.isclose(actual_realized, expected_realized)
