from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from gambit.account import Account
from gambit.pq_types import (
    Contract,
    ContractGroup,
    LimitOrder,
    MarketOrder,
    OrderStatus,
    StopLimitOrder,
    TimeInForce,
    Trade,
    VWAPOrder,
)
from gambit.strategy import Strategy
from gambit.strategy_components import VWAPMarketSimulator


def _constant_price(_contract, _timestamps, _index, _context):
    return 100.0


def test_partial_fill_transitions_to_filled() -> None:
    contract = Contract.create("PARTIAL", ContractGroup.get("orders"))
    order = MarketOrder(
        contract=contract,
        timestamp=np.datetime64("2024-01-02T09:30"),
        qty=10,
        time_in_force=TimeInForce.GTC,
    )

    order.fill(4)
    assert order.qty == 6
    assert order.status is OrderStatus.PARTIALLY_FILLED

    order.fill(6)
    assert order.qty == 0
    assert order.status is OrderStatus.FILLED
    assert not order.is_open()


def test_terminal_order_lifecycle_operations_are_idempotent() -> None:
    timestamp = np.datetime64("2024-01-02T09:30")
    contract = Contract.create("TERMINAL-LIFECYCLE", ContractGroup.get("orders"))
    filled_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    filled_order.fill()

    filled_order.request_cancel()
    filled_order.cancel()

    assert filled_order.status is OrderStatus.FILLED

    cancelled_order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    cancelled_order.cancel()
    cancelled_order.request_cancel()
    cancelled_order.cancel()

    assert cancelled_order.status is OrderStatus.CANCELLED


@pytest.mark.parametrize("qty", [0.0, np.nan, np.inf, -np.inf])
def test_market_order_rejects_invalid_quantity(qty: float) -> None:
    contract = Contract.create("INVALID", ContractGroup.get("orders"))

    with pytest.raises(ValueError, match="finite and nonzero"):
        MarketOrder(contract=contract, qty=qty)


@pytest.mark.parametrize("qty", [0.5, -1.5, True])
def test_market_order_rejects_non_whole_quantity(qty) -> None:
    contract = Contract.create("FRACTIONAL-ORDER", ContractGroup.get("orders"))

    with pytest.raises(ValueError, match="whole shares or contracts"):
        MarketOrder(contract=contract, qty=qty)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("contract", object(), "contract must be a Contract"),
        ("timestamp", "2024-01-02", "timestamp must be a numpy datetime64 value"),
        ("time_in_force", "GTC", "time_in_force must be a TimeInForce"),
        ("status", "OPEN", "status must be an OrderStatus"),
    ],
)
def test_order_rejects_invalid_entity_fields(field: str, value: object, message: str) -> None:
    kwargs = {
        "contract": Contract.create("INVALID-ORDER-FIELD", ContractGroup.get("orders")),
        "timestamp": np.datetime64("2024-01-02"),
        "qty": 1,
        field: value,
    }

    with pytest.raises(TypeError, match=message):
        MarketOrder(**kwargs)


def test_order_rejects_fractional_fill_without_mutating_remaining_quantity() -> None:
    contract = Contract.create("FRACTIONAL-FILL", ContractGroup.get("orders"))
    order = MarketOrder(contract=contract, qty=10)

    with pytest.raises(ValueError, match="whole shares or contracts"):
        order.fill(0.5)

    assert order.qty == 10
    assert order.status is OrderStatus.OPEN


@pytest.mark.parametrize(
    ("fill_qty", "message"),
    [
        (-1, "opposite sign"),
        (11, "larger than order qty"),
        ("one", "whole shares or contracts"),
    ],
)
def test_order_rejects_invalid_fill_atomically(fill_qty: object, message: str) -> None:
    contract = Contract.create("INVALID-FILL", ContractGroup.get("orders"))
    order = MarketOrder(contract=contract, qty=10)

    with pytest.raises(ValueError, match=message):
        order.fill(fill_qty)  # type: ignore[arg-type]

    assert order.qty == 10
    assert order.status is OrderStatus.OPEN


def test_order_rejects_fill_after_terminal_state() -> None:
    contract = Contract.create("TERMINAL-FILL", ContractGroup.get("orders"))
    order = MarketOrder(contract=contract, qty=1)
    order.fill()

    with pytest.raises(ValueError, match="cannot fill an order in status"):
        order.fill()

    assert order.qty == 0
    assert order.status is OrderStatus.FILLED


@pytest.mark.parametrize("limit_price", [np.nan, np.inf, -np.inf, True, "100"])
def test_limit_order_rejects_invalid_limit_price(limit_price) -> None:
    contract = Contract.create("INVALID-LIMIT-PRICE", ContractGroup.get("orders"))

    with pytest.raises(ValueError, match="limit price must be a finite real number"):
        LimitOrder(contract=contract, qty=1, limit_price=limit_price)


@pytest.mark.parametrize("trigger_price", [np.nan, np.inf, -np.inf, True, "100"])
def test_stop_limit_order_rejects_invalid_trigger_price(trigger_price) -> None:
    contract = Contract.create("INVALID-TRIGGER-PRICE", ContractGroup.get("orders"))

    with pytest.raises(ValueError, match="stop trigger price must be a finite real number"):
        StopLimitOrder(contract=contract, qty=1, trigger_price=trigger_price)


def test_stop_limit_order_allows_market_trigger_without_limit() -> None:
    contract = Contract.create("STOP-MARKET", ContractGroup.get("orders"))

    order = StopLimitOrder(contract=contract, qty=-1, trigger_price=95)

    assert order.trigger_price == 95.0
    assert np.isnan(order.limit_price)


@pytest.mark.parametrize("limit_price", [np.inf, -np.inf, True, "90"])
def test_stop_limit_order_rejects_invalid_explicit_limit(limit_price) -> None:
    contract = Contract.create("INVALID-STOP-LIMIT", ContractGroup.get("orders"))

    with pytest.raises(ValueError, match="stop limit price must be a finite real number"):
        StopLimitOrder(contract=contract, qty=-1, trigger_price=95, limit_price=limit_price)


def test_unfilled_fok_order_is_cancelled_after_fill_window() -> None:
    group = ContractGroup.get("fok")
    contract = Contract.create("FOK", group)
    timestamps = np.array(["2024-01-02T09:30", "2024-01-02T09:31"], dtype="datetime64[ns]")
    strategy = Strategy(timestamps, [group], _constant_price, trade_lag=0)
    order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=1, time_in_force=TimeInForce.FOK)
    strategy._current_orders = [order]

    strategy._sim_market(1)

    assert order.status is OrderStatus.CANCELLED


def test_cancel_request_is_acknowledged_before_market_simulation() -> None:
    group = ContractGroup.get("cancel-request")
    contract = Contract.create("CANCEL", group)
    timestamp = np.datetime64("2024-01-02T09:30", "ns")
    strategy = Strategy(np.array([timestamp]), [group], _constant_price)
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1, time_in_force=TimeInForce.GTC)
    order.request_cancel()
    strategy._current_orders = [order]

    strategy._sim_market(0)

    assert order.status is OrderStatus.CANCELLED


def test_vwap_stop_cancels_when_prorated_fill_is_below_one_contract() -> None:
    group = ContractGroup.get("zero-vwap-fill")
    contract = Contract.create("ZERO-VWAP-FILL", group)
    timestamps = np.array(["2024-01-02T09:30", "2024-01-02T09:31"], dtype="datetime64[ns]")
    order = VWAPOrder(
        contract=contract,
        timestamp=timestamps[0],
        qty=1,
        vwap_end_time=timestamps[0] + np.timedelta64(10, "m"),
        vwap_stop=101.0,
    )
    indicators = {group.name: SimpleNamespace(price=np.array([100.0, 100.0]), volume=np.array([10.0, 10.0]))}
    simulator = VWAPMarketSimulator("price", "volume")

    trades = simulator([order], 1, timestamps, indicators, {}, SimpleNamespace())

    assert trades == []
    assert order.status is OrderStatus.CANCELLED


def test_vwap_sell_uses_backup_price_when_market_data_is_missing() -> None:
    group = ContractGroup.get("sell-vwap-backup")
    contract = Contract.create("SELL-VWAP-BACKUP", group)
    timestamp = np.datetime64("2024-01-02T09:30")
    timestamps = np.array([timestamp])
    order = VWAPOrder(contract=contract, timestamp=timestamp, qty=-2, vwap_end_time=timestamp)
    indicators = {
        group.name: SimpleNamespace(
            price=np.array([0.0]),
            volume=np.array([0.0]),
            backup=np.array([99.0]),
        )
    }
    simulator = VWAPMarketSimulator("price", "volume", "backup")

    trades = simulator([order], 0, timestamps, indicators, {}, SimpleNamespace())

    assert len(trades) == 1
    assert trades[0].qty == -2
    assert trades[0].price == 99.0
    assert order.status is OrderStatus.FILLED


def test_vwap_rejects_invalid_backup_price_before_mutating_order() -> None:
    group = ContractGroup.get("invalid-vwap-backup")
    contract = Contract.create("INVALID-VWAP-BACKUP", group)
    timestamp = np.datetime64("2024-01-02T09:30")
    timestamps = np.array([timestamp])
    order = VWAPOrder(contract=contract, timestamp=timestamp, qty=2, vwap_end_time=timestamp)
    indicators = {
        group.name: SimpleNamespace(
            price=np.array([0.0]),
            volume=np.array([0.0]),
            backup=np.array([np.inf]),
        )
    }
    simulator = VWAPMarketSimulator("price", "volume", "backup")

    with pytest.raises(ValueError, match="non-finite price"):
        simulator([order], 0, timestamps, indicators, {}, SimpleNamespace())

    assert order.qty == 2
    assert order.status is OrderStatus.OPEN


def test_immediate_vwap_stop_fills_without_dividing_by_zero() -> None:
    group = ContractGroup.get("immediate-vwap-stop")
    contract = Contract.create("IMMEDIATE-VWAP-STOP", group)
    timestamp = np.datetime64("2024-01-02T09:30")
    timestamps = np.array([timestamp])
    order = VWAPOrder(
        contract=contract,
        timestamp=timestamp,
        qty=2,
        vwap_end_time=timestamp,
        vwap_stop=101.0,
    )
    indicators = {group.name: SimpleNamespace(price=np.array([100.0]), volume=np.array([10.0]))}
    simulator = VWAPMarketSimulator("price", "volume")

    trades = simulator([order], 0, timestamps, indicators, {}, SimpleNamespace())

    assert len(trades) == 1
    assert trades[0].qty == 2
    assert trades[0].price == 100.0


@pytest.mark.parametrize(
    ("timestamp", "end_time", "error", "message"),
    [
        (np.datetime64("2024-01-02T09:31"), np.datetime64("2024-01-02T09:30"), ValueError, "cannot precede"),
        (np.datetime64("NaT"), np.datetime64("2024-01-02T09:30"), ValueError, "cannot be NaT"),
        (np.datetime64("2024-01-02T09:30"), "2024-01-02T09:31", TypeError, "numpy datetime64"),
    ],
)
def test_vwap_order_rejects_invalid_execution_window(timestamp, end_time, error, message) -> None:
    contract = Contract.create("INVALID-VWAP-WINDOW", ContractGroup.get("invalid-vwap-window"))

    with pytest.raises(error, match=message):
        VWAPOrder(contract=contract, timestamp=timestamp, qty=1, vwap_end_time=end_time)


def test_multiplier_aware_trade_reversal_reconciles_realized_and_unrealized_pnl() -> None:
    group = ContractGroup.get("reversal")
    contract = Contract.create("REVERSAL", group, multiplier=10.0)
    timestamps = np.array(["2024-01-02T09:30", "2024-01-02T10:30"], dtype="datetime64[ns]")
    context = SimpleNamespace(prices=np.array([100.0, 105.0]))

    def mark_price(_contract, _timestamps, index, strategy_context):
        return strategy_context.prices[index]

    account = Account([group], timestamps, mark_price, context, starting_equity=1_000.0)
    trades = [
        Trade(contract, MarketOrder(contract=contract, timestamp=timestamps[0], qty=3), timestamps[0], 3, 100.0),
        Trade(contract, MarketOrder(contract=contract, timestamp=timestamps[1], qty=-5), timestamps[1], -5, 110.0),
    ]

    account.add_trades(trades)
    account.calc(timestamps[-1])
    pnl = account.symbol_pnls[contract.symbol].df().row(-1, named=True)

    assert pnl["position"] == -2
    assert np.isclose(pnl["realized"], 300.0)
    assert np.isclose(pnl["unrealized"], 100.0)
    assert np.isclose(pnl["net_pnl"], 400.0)
    assert np.isclose(account.equity(timestamps[-1]), 1_400.0)


def test_expired_contract_pnl_is_frozen_after_first_post_expiry_mark() -> None:
    group = ContractGroup.get("expiry")
    timestamps = np.array(
        ["2024-01-02T09:30", "2024-01-02T10:30", "2024-01-02T11:30", "2024-01-02T12:30"],
        dtype="datetime64[ns]",
    )
    contract = Contract.create("EXPIRY", group, expiry=timestamps[1], multiplier=10.0)
    context = SimpleNamespace(prices=np.array([100.0, 105.0, 110.0, 200.0]))

    def mark_price(_contract, _timestamps, index, strategy_context):
        return strategy_context.prices[index]

    account = Account([group], timestamps, mark_price, context, starting_equity=1_000.0)
    account.add_trades(
        [
            Trade(
                contract,
                MarketOrder(contract=contract, timestamp=timestamps[0], qty=1),
                timestamps[0],
                1,
                100.0,
            )
        ]
    )

    first_post_expiry_equity = account.equity(timestamps[2])
    later_equity = account.equity(timestamps[3])

    assert np.isclose(first_post_expiry_equity, 1_100.0)
    assert np.isclose(later_equity, first_post_expiry_equity)
