from types import SimpleNamespace

import numpy as np
import pytest

from gambit.account import Account
from gambit.boundaries import BacktestCallbackError
from gambit.pq_types import Contract, ContractGroup, MarketOrder, OrderStatus, Trade
from gambit.strategy import Strategy


def price(*_args):
    return 100.0


def setup_trade():
    group = ContractGroup.get("trade-numerics")
    contract = Contract.create("NUMERIC", group)
    timestamps = np.array(["2026-01-02T09:30", "2026-01-02T09:31"], dtype="datetime64[ns]")
    order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=2)
    trade = Trade(contract, order, timestamps[0], 1, 100)
    return group, timestamps, order, trade


@pytest.mark.parametrize("field", ["price", "fee", "commission"])
@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf, True, "100"])
def test_mutated_simulator_financial_values_fail_before_ledger_changes(field, value):
    group, timestamps, order, trade = setup_trade()
    strategy = Strategy(timestamps, [group], price, trade_lag=0, log_trades=False)
    strategy._current_orders = [order]

    def simulator(*_args):
        order.fill(1)
        setattr(trade, field, value)
        return [trade]

    strategy.add_market_sim(simulator)
    with pytest.raises(BacktestCallbackError) as error:
        strategy._sim_market(0)
    assert f"trade {field} must be a finite real number" in str(error.value.__cause__)
    assert order.qty == 2 and order.status is OrderStatus.OPEN
    assert strategy.account.trade_count == 0
    assert strategy.account.symbols() == []


@pytest.mark.parametrize("field,value", [
    ("qty", 0), ("qty", 0.5), ("qty", np.nan), ("qty", np.inf), ("qty", True),
    ("price", np.nan), ("price", np.inf), ("price", True), ("price", "100"),
    ("fee", np.nan), ("fee", -np.inf), ("commission", np.nan), ("commission", np.inf),
])
def test_account_revalidates_mutated_trade_numbers_before_batch_mutation(field, value):
    group, timestamps, order, trade = setup_trade()
    account = Account([group], timestamps, price, SimpleNamespace())
    valid = Trade(order.contract, order, timestamps[0], 1, 100)
    setattr(trade, field, value)

    with pytest.raises(ValueError, match=f"trade {field}"):
        account.add_trades([valid, trade])
    assert account.trade_count == 0
    assert account.symbols() == []
    assert account.trades() == []
    assert order.qty == 2 and order.status is OrderStatus.OPEN


def test_rejected_numeric_batch_preserves_existing_history_and_valuation():
    group, timestamps, order, trade = setup_trade()
    account = Account([group], timestamps, price, SimpleNamespace())
    account.add_trades([trade])
    equity = account.equity(timestamps[0])
    before = account.df_pnl()
    invalid = Trade(order.contract, order, timestamps[1], 1, 101)
    invalid.fee = np.nan

    with pytest.raises(ValueError, match="trade fee"):
        account.add_trades([invalid])
    assert account.trade_count == 1
    assert account.position(group, timestamps[-1]) == 1
    assert account.equity(timestamps[0]) == equity
    assert account.df_pnl().equals(before)


@pytest.mark.parametrize("execution_price", [0.0, -10.0, 100.0])
def test_finite_negative_prices_and_fee_rebates_remain_supported(execution_price):
    group, timestamps, order, trade = setup_trade()
    account = Account([group], timestamps, price, SimpleNamespace())
    trade.price = execution_price
    trade.fee = -1.0
    trade.commission = -2.0
    account.add_trades([trade])
    stored = account.trades()[0]
    assert (stored.price, stored.fee, stored.commission) == (execution_price, -1.0, -2.0)
    assert account.position(group, timestamps[0]) == 1
    assert np.isfinite(account.equity(timestamps[0]))
