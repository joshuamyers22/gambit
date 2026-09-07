"""Exercise installed-package order sizing and accounting without optional extras."""

from types import SimpleNamespace

import numpy as np

from gambit.account import Account
from gambit.pq_types import Contract, ContractGroup
from gambit.strategy_components import PercentOfEquityTradingRule, SimpleMarketSimulator


def main() -> None:
    Contract.clear_cache()
    ContractGroup.clear_cache()
    group = ContractGroup.get("release-smoke")
    contract = Contract.create("MULTIPLIER-50", group, multiplier=50)
    timestamps = np.array(["2026-01-02T09:30"], dtype="datetime64[ns]")

    def price(*_args):
        return 100.0

    context = SimpleNamespace()
    account = Account([group], timestamps, price, context, starting_equity=100_000)
    rule = PercentOfEquityTradingRule("release-smoke", price, equity_percent=0.1)
    orders = rule(group, 0, timestamps, SimpleNamespace(), np.array([True]), account, (), context)
    assert len(orders) == 1 and orders[0].qty == 2, "multiplier-aware sizing failed"
    trades = SimpleMarketSimulator(price)(orders, 0, timestamps, {}, {}, context)
    account.add_trades(trades)
    assert account.positions(group, timestamps[0]) == [(contract, 2)]
    assert account.equity(timestamps[0]) == 100_000
    print("verified installed order sizing, execution, and account reconciliation")


if __name__ == "__main__":
    main()
