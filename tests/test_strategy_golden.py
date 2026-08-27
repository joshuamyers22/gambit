from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from gambit.pq_types import Contract, ContractGroup, MarketOrder
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator


def test_short_scale_in_and_out_reconciles_end_to_end() -> None:
    group = ContractGroup.get("golden-short")
    contract = Contract.create("GOLDEN-SHORT", group)
    timestamps = np.array(
        [
            "2024-01-02T09:30",
            "2024-01-02T09:31",
            "2024-01-02T09:32",
            "2024-01-02T09:33",
            "2024-01-02T15:00",
        ],
        dtype="datetime64[ns]",
    )
    context = SimpleNamespace(prices=np.array([100.0, 90.0, 80.0, 70.0, 70.0]))
    quantities = np.array([-2, -3, 1, 4, 0])

    def price_function(_contract, _timestamps, index, strategy_context):
        return strategy_context.prices[index]

    def rebalance_signal(_group, _timestamps, _indicators, _signals, _context):
        return quantities != 0

    def rebalance_rule(
        _group,
        index,
        rule_timestamps,
        _indicators,
        _signal,
        _account,
        _orders,
        _context,
    ):
        quantity = int(quantities[index])
        reason = "SCALE_SHORT" if quantity < 0 else "COVER_SHORT"
        return [
            MarketOrder(
                contract=contract,
                timestamp=rule_timestamps[index],
                qty=quantity,
                reason_code=reason,
            )
        ]

    strategy = Strategy(
        timestamps,
        [group],
        price_function,
        trade_lag=0,
        starting_equity=1_000.0,
        strategy_context=context,
    )
    strategy.add_signal("rebalance", rebalance_signal)
    strategy.add_rule("rebalance", rebalance_rule, signal_name="rebalance")
    strategy.add_market_sim(SimpleMarketSimulator(price_function))

    strategy.run()

    trades = strategy.trades()
    assert [trade.qty for trade in trades] == [-2, -3, 1, 4]
    assert [trade.order.reason_code for trade in trades] == [
        "SCALE_SHORT",
        "SCALE_SHORT",
        "COVER_SHORT",
        "COVER_SHORT",
    ]
    assert strategy.account.position(group, timestamps[-1]) == 0

    roundtrips = strategy.roundtrip_trades()
    completed = [trade for trade in roundtrips if not np.isnat(trade.exit_timestamp)]
    assert len(completed) == 3
    assert np.isclose(sum(trade.net_pnl for trade in completed), 110.0)

    final_pnl = strategy.df_pnl(group).row(-1, named=True)
    assert np.isclose(final_pnl["realized"], 110.0)
    assert np.isclose(final_pnl["unrealized"], 0.0)
    assert np.isclose(final_pnl["net_pnl"], 110.0)
    assert np.isclose(strategy.account.equity(timestamps[-1]), 1_110.0)
