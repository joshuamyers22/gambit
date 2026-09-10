from types import SimpleNamespace

import numpy as np
import pytest

from gambit.pq_types import Contract, ContractGroup, OrderStatus, TimeInForce, VWAPOrder
from gambit.pq_utils import PQException
from gambit.strategy import Strategy
from gambit.strategy_components import VWAPMarketSimulator


def setup_market():
    group = ContractGroup.get("vwap-causality")
    contract = Contract.create("VWAP-CAUSAL", group)
    timestamps = np.array(["2026-09-10T23:57", "2026-09-10T23:58", "2026-09-10T23:59",
                           "2026-09-11T00:00", "2026-09-11T00:01"], dtype="datetime64[ns]")
    return group, contract, timestamps


def order_for(contract, timestamps, sign=1, end_index=4, stop=np.nan):
    return VWAPOrder(contract=contract, timestamp=timestamps[1], qty=sign * 8,
                     time_in_force=TimeInForce.GTC, vwap_end_time=timestamps[end_index], vwap_stop=stop)


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("end_index", [3, 4])
@pytest.mark.parametrize("future_price,future_volume", [(1.0, 1.0), (1000.0, 10000.0),
                                                       (np.nan, 0.0), (np.inf, np.inf)])
def test_day_end_fill_ignores_all_future_market_values(sign, end_index, future_price, future_volume):
    group, contract, timestamps = setup_market()
    order = order_for(contract, timestamps, sign, end_index)
    prices = np.array([9999.0, 100.0, 104.0, future_price, future_price])
    volumes = np.array([1000.0, 1.0, 3.0, future_volume, future_volume])
    indicators = {group.name: SimpleNamespace(price=prices, volume=volumes)}
    simulator = VWAPMarketSimulator("price", "volume")
    full = simulator([order], 2, timestamps, indicators, {}, SimpleNamespace())
    # Replaying only the available prefix must give the same fill. Truncation
    # uses the existing final-heartbeat trigger instead of the day-end trigger.
    prefix_order = order_for(contract, timestamps, sign, end_index)
    prefix = simulator([prefix_order], 2, timestamps[:3],
                       {group.name: SimpleNamespace(price=prices[:3], volume=volumes[:3])}, {}, SimpleNamespace())
    assert len(full) == len(prefix) == 1
    assert full[0].price == prefix[0].price == 103.0
    assert full[0].qty == prefix[0].qty == sign * 8
    assert full[0].timestamp == prefix[0].timestamp == timestamps[2]
    assert order.status is prefix_order.status is OrderStatus.FILLED


@pytest.mark.parametrize("sign", [1, -1])
def test_future_liquidity_cannot_replace_missing_historical_data(sign):
    group, contract, timestamps = setup_market()
    order = order_for(contract, timestamps, sign)
    indicators = {group.name: SimpleNamespace(price=np.array([9000., 0., np.nan, 1000., 1000.]),
                                             volume=np.ones(5), backup=np.array([1., 2., 97., 999., 999.]))}
    trades = VWAPMarketSimulator("price", "volume", "backup")(
        [order], 2, timestamps, indicators, {}, SimpleNamespace())
    assert len(trades) == 1 and trades[0].price == 97.0
    assert trades[0].timestamp == timestamps[2] and trades[0].qty == sign * 8


def test_future_liquidity_cannot_hide_missing_backup_configuration():
    group, contract, timestamps = setup_market()
    order = order_for(contract, timestamps)
    indicators = {group.name: SimpleNamespace(price=np.array([9000., 0., np.nan, 1000., 1000.]),
                                             volume=np.ones(5))}
    with pytest.raises(PQException, match="backup price indicator not found"):
        VWAPMarketSimulator("price", "volume")([order], 2, timestamps, indicators, {}, SimpleNamespace())
    assert order.status is OrderStatus.OPEN and order.qty == 8


@pytest.mark.parametrize("sign", [1, -1])
def test_regular_expiry_excludes_observations_after_requested_end(sign):
    group, contract, timestamps = setup_market()
    order = order_for(contract, timestamps, sign, end_index=1)
    indicators = {group.name: SimpleNamespace(price=np.array([9999., 100., 104., 1000., 1000.]),
                                             volume=np.array([1000., 1., 3., 9., 9.]))}
    trades = VWAPMarketSimulator("price", "volume")([order], 2, timestamps, indicators, {}, SimpleNamespace())
    assert len(trades) == 1 and trades[0].price == 100.0
    assert trades[0].timestamp == timestamps[2]


@pytest.mark.parametrize("sign", [1, -1])
def test_stop_uses_only_elapsed_prices_and_preserves_prorating(sign):
    group, contract, timestamps = setup_market()
    stop = 105.0 if sign == 1 else 103.0
    order = order_for(contract, timestamps, sign, stop=stop)
    indicators = {group.name: SimpleNamespace(price=np.array([9999., 100., 104., 1000., 1000.]),
                                             volume=np.array([1000., 1., 3., 9., 9.]))}
    trades = VWAPMarketSimulator("price", "volume")([order], 2, timestamps, indicators, {}, SimpleNamespace())
    assert len(trades) == 1 and trades[0].price == 103.0
    assert trades[0].qty == sign * 2  # one elapsed minute of a three-minute window
    assert order.qty == sign * 6 and order.status is OrderStatus.CANCELLED


def test_order_waits_before_expiry_when_no_day_boundary_or_stop():
    group, contract, timestamps = setup_market()
    order = order_for(contract, timestamps)
    indicators = {group.name: SimpleNamespace(price=np.full(5, 100.0), volume=np.ones(5))}
    trades = VWAPMarketSimulator("price", "volume")([order], 1, timestamps, indicators, {}, SimpleNamespace())
    assert trades == [] and order.status is OrderStatus.OPEN and order.qty == 8


@pytest.mark.parametrize("sign", [1, -1])
@pytest.mark.parametrize("future_price", [1.0, 1000.0])
def test_strategy_records_causal_vwap_fill_and_marked_equity(sign, future_price):
    group, contract, timestamps = setup_market()
    prices = np.array([9999., 100., 104., future_price, future_price])
    strategy = Strategy(timestamps, [group], lambda *_: 104.0, trade_lag=0, log_trades=False)
    strategy.add_indicator("price", lambda *_: prices)
    strategy.add_indicator("volume", lambda *_: np.array([1000., 1., 3., 10000., 10000.]))
    strategy.add_signal("entry", lambda *_: np.array([False, True, False, False, False]))
    strategy.add_rule("entry", lambda *_: [order_for(contract, timestamps, sign)], "entry")
    strategy.add_market_sim(VWAPMarketSimulator("price", "volume"))
    strategy.run()
    trade, = strategy.trades()
    assert trade.price == 103.0 and trade.timestamp == timestamps[2] and trade.qty == sign * 8
    # A constant independent mark makes the accounting effect directly visible.
    assert strategy.account.equity(timestamps[-1]) == strategy.account.starting_equity + sign * 8
