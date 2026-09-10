from datetime import datetime
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
    group = ContractGroup.get("trade-references")
    contract = Contract.create("TRADE-REFERENCE", group)
    timestamps = np.array(["2026-01-02T09:30", "2026-01-02T09:31"], dtype="datetime64[ns]")
    order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=2)
    trade = Trade(contract, order, timestamps[0], 1, 100)
    return group, timestamps, order, trade


def invalidate(case, timestamps, order, trade):
    if case == "future_order":
        order.timestamp = timestamps[1]
    elif case == "nat_order":
        order.timestamp = np.datetime64("NaT", "ns")
    elif case == "string_order":
        order.timestamp = "2026-01-02T09:30"
    elif case == "datetime_trade":
        trade.timestamp = datetime(2026, 1, 2, 9, 30)
    elif case == "nat_trade":
        trade.timestamp = np.datetime64("NaT", "ns")
    elif case == "duck_order":
        trade.order = SimpleNamespace(contract=order.contract, timestamp=order.timestamp,
                                      qty=order.qty, status=order.status, properties=SimpleNamespace())
    elif case == "invalid_contract":
        trade.contract = SimpleNamespace(symbol=order.contract.symbol, contract_group=order.contract.contract_group)
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case,error,message", [
    ("future_order", ValueError, "cannot precede"),
    ("nat_order", ValueError, "order timestamp must be a valid"),
    ("string_order", ValueError, "order timestamp must be a valid"),
    ("datetime_trade", TypeError, "timestamp must be a numpy datetime64"),
    ("nat_trade", ValueError, "timestamp cannot be NaT"),
    ("duck_order", TypeError, "order must be an Order"),
    ("invalid_contract", TypeError, "contract must be a Contract"),
])
def test_account_revalidates_reference_and_time_invariants(case, error, message):
    group, timestamps, order, trade = setup_trade()
    account = Account([group], timestamps, price, SimpleNamespace())
    valid_order = MarketOrder(contract=order.contract, timestamp=timestamps[0], qty=1)
    valid = Trade(order.contract, valid_order, timestamps[0], 1, 100)
    invalidate(case, timestamps, order, trade)

    with pytest.raises(error, match=message):
        account.add_trades([valid, trade])
    assert account.trade_count == 0
    assert account.symbols() == []


def test_mutated_order_timestamp_cannot_corrupt_existing_history():
    group, timestamps, order, trade = setup_trade()
    account = Account([group], timestamps, price, SimpleNamespace())
    account.add_trades([trade])
    before = account.df_pnl()
    order.timestamp = timestamps[1]

    with pytest.raises(ValueError, match="cannot precede"):
        account.add_trades([trade])
    assert account.trade_count == 1
    assert account.trades()[0].order.timestamp == timestamps[0]
    assert account.df_pnl().equals(before)


@pytest.mark.parametrize("case,message", [
    ("future_order", "cannot precede"),
    ("nat_order", "order timestamp must be a valid"),
    ("datetime_trade", "timestamp must be a numpy datetime64"),
])
def test_simulator_revalidates_mutated_time_fields(case, message):
    group, timestamps, order, trade = setup_trade()
    strategy = Strategy(timestamps, [group], price, trade_lag=0, log_trades=False)
    strategy._current_orders = [order]

    def simulator(*_args):
        order.fill(1)
        invalidate(case, timestamps, order, trade)
        return [trade]

    strategy.add_market_sim(simulator)
    with pytest.raises(BacktestCallbackError) as error:
        strategy._sim_market(0)
    assert message in str(error.value.__cause__)
    assert order.qty == 2 and order.status is OrderStatus.OPEN
    assert strategy.trades() == []


def test_direct_import_accepts_filled_order_and_earlier_off_grid_submission():
    group, timestamps, order, trade = setup_trade()
    account = Account([group], timestamps, price, SimpleNamespace())
    order.timestamp = np.datetime64("2026-01-02T09:29:30", "ns")
    order.fill()
    account.add_trades([trade])
    assert account.trade_count == 1
    assert account.position(group, timestamps[0]) == 1
