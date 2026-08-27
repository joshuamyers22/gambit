from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import numpy as np
from gambit.compute_pnl import calc_trade_pnl

from gambit.account import Account
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Trade


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


def test_golden_account_scenario_reconciles_equity_and_pnl() -> None:
    group = ContractGroup.get("golden-account")
    contract = Contract.create("GOLDEN", group)
    timestamps = np.array(["2024-01-02T09:30", "2024-01-02T10:30", "2024-01-02T15:00"], dtype="datetime64[ns]")
    context = SimpleNamespace(prices=np.array([100.0, 110.0, 90.0]))

    def mark_price(_contract, _timestamps, index, strategy_context):
        return strategy_context.prices[index]

    account = Account([group], timestamps, mark_price, context, starting_equity=1_000.0)
    fills = [(10, 100.0, 2.0, 1.0), (-4, 110.0, 1.0, 0.5), (-6, 90.0, 1.5, 0.75)]
    trades = [
        Trade(
            contract,
            MarketOrder(contract=contract, timestamp=timestamp, qty=qty),
            timestamp,
            qty,
            price,
            fee,
            commission,
        )
        for timestamp, (qty, price, fee, commission) in zip(timestamps, fills)
    ]

    account.add_trades(trades)
    account.calc(timestamps[-1])

    pnl = account.symbol_pnls[contract.symbol].df()
    assert pnl["position"].to_list() == [10, 6, 0]
    assert np.allclose(pnl["realized"].to_numpy(), [0.0, 40.0, -20.0])
    assert np.allclose(pnl["unrealized"].to_numpy(), [0.0, 60.0, 0.0])
    assert np.allclose(pnl["commission"].to_numpy(), [1.0, 1.5, 2.25])
    assert np.allclose(pnl["fee"].to_numpy(), [2.0, 3.0, 4.5])
    assert np.allclose(pnl["net_pnl"].to_numpy(), [-3.0, 95.5, -26.75])
    assert np.isclose(account.equity(timestamps[-1]), 973.25)
    assert np.isclose(account.equity(timestamps[-1]) - account.starting_equity, pnl["net_pnl"][-1])

    early_roundtrips = account.roundtrip_trades(end_date=timestamps[1])
    completed = [trade for trade in early_roundtrips if not np.isnat(trade.exit_timestamp)]
    assert len(completed) == 1
    assert completed[0].exit_timestamp == timestamps[1]
