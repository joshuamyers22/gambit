"""Pure reconciliation and projection of executions into round-trip trades."""

from __future__ import annotations

import copy
from collections import defaultdict, deque
from types import SimpleNamespace

import numpy as np
import polars as pl

from gambit.pq_types import RoundTripTrade, Trade


def _net_trade(stack: deque[Trade], trade: Trade) -> RoundTripTrade | None:
    if not stack or np.sign(trade.qty) == np.sign(stack[0].qty):
        stack.append(trade)
        return None

    entry = stack[0]
    qty = float(min(abs(entry.qty), abs(trade.qty)) * np.sign(entry.qty))
    entry_fraction = abs(qty / entry.qty)
    exit_fraction = abs(qty / trade.qty)
    pnl = float(
        qty * (trade.price - entry.price) * entry.contract.multiplier
        - trade.commission * exit_fraction
        - entry.commission * entry_fraction
    )
    roundtrip = RoundTripTrade(
        entry.contract,
        entry.order,
        trade.order,
        entry.timestamp,
        trade.timestamp,
        qty,
        entry.price,
        trade.price,
        entry.order.reason_code if entry.order else "",
        trade.order.reason_code if trade.order else "",
        entry.commission * entry_fraction,
        trade.commission * exit_fraction,
        copy.deepcopy(entry.properties),
        copy.deepcopy(trade.properties),
        pnl,
    )
    residual = entry.qty - qty
    entry.qty -= qty
    entry.commission *= 1 - entry_fraction
    trade.qty += qty
    trade.commission *= 1 - exit_fraction
    if residual == 0:
        stack.popleft()
    return roundtrip


def roundtrip_trades(trades: list[Trade]) -> list[RoundTripTrade]:
    """Reconcile ordered executions without mutating the source trades.

    >>> qtys = [100, -50, 20, -120, 10]
    >>> prices = [9, 10, 8, 11, 12]
    >>> contract = SimpleNamespace(symbol='AAPL', multiplier=1)
    >>> order = SimpleNamespace(reason_code='DUMMY')
    >>> trades = [Trade(contract, order, np.datetime64('2022-11-05 08:00') + np.timedelta64(i, 'm'), qty, prices[i]) for i, qty in enumerate(qtys)]
    >>> [(rt.qty, rt.entry_price, rt.exit_price, rt.net_pnl) for rt in roundtrip_trades(trades)]
    [(50.0, 9, 10, 50.0), (50.0, 9, 11, 100.0), (20.0, 8, 11, 60.0), (-10.0, 11, 12, -10.0), (-40.0, 11, nan, 0.0)]
    """
    reconciled: list[RoundTripTrade] = []
    stacks: dict[str, deque[Trade]] = defaultdict(deque)
    working_trades: list[Trade] = []
    for index, source_trade in enumerate(trades):
        trade = copy.copy(source_trade)
        trade.properties = copy.deepcopy(source_trade.properties)
        trade.properties.index = index
        working_trades.append(trade)

    for trade in working_trades:
        while True:
            roundtrip = _net_trade(stacks[trade.contract.symbol], trade)
            if roundtrip is None:
                break
            reconciled.append(roundtrip)
            if trade.qty == 0:
                break

    for open_trade in (trade for stack in stacks.values() for trade in stack):
        reconciled.append(
            RoundTripTrade(
                open_trade.contract,
                open_trade.order,
                None,
                open_trade.timestamp,
                np.datetime64("NaT", "ns"),
                open_trade.qty,
                open_trade.price,
                np.nan,
                open_trade.order.reason_code,
                None,
                open_trade.commission,
                np.nan,
                open_trade.properties,
                SimpleNamespace(),
                0.0,
            )
        )
    reconciled.sort(key=lambda item: item.entry_properties.index)
    for index, roundtrip in enumerate(reconciled):
        roundtrip.entry_properties.entry_index = roundtrip.entry_properties.index
        roundtrip.entry_properties.index = index
    return reconciled


def df_roundtrip_trade(roundtrips: list[RoundTripTrade]) -> pl.DataFrame:
    """Project round-trip trades into the stable tabular representation."""
    frame = pl.DataFrame(
        {
            "symbol": [trade.contract.symbol for trade in roundtrips],
            "multiplier": np.asarray([trade.contract.multiplier for trade in roundtrips], dtype=float),
            "entry_timestamp": np.asarray([trade.entry_timestamp for trade in roundtrips], dtype="datetime64[ns]"),
            "exit_timestamp": np.asarray([trade.exit_timestamp for trade in roundtrips], dtype="datetime64[ns]"),
            "qty": np.asarray([trade.qty for trade in roundtrips], dtype=float),
            "entry_price": np.asarray([trade.entry_price for trade in roundtrips], dtype=float),
            "exit_price": np.asarray([trade.exit_price for trade in roundtrips], dtype=float),
            "entry_reason": [trade.entry_reason for trade in roundtrips],
            "exit_reason": [trade.exit_reason for trade in roundtrips],
            "entry_commission": np.asarray([trade.entry_commission for trade in roundtrips], dtype=float),
            "exit_commission": np.asarray([trade.exit_commission for trade in roundtrips], dtype=float),
            "net_pnl": np.asarray([trade.net_pnl for trade in roundtrips], dtype=float),
        },
        schema_overrides={
            "symbol": pl.String,
            "entry_timestamp": pl.Datetime("ns"),
            "exit_timestamp": pl.Datetime("ns"),
            "entry_reason": pl.String,
            "exit_reason": pl.String,
        },
    )
    return frame.sort(["entry_timestamp", "symbol"]) if len(frame) else frame


__all__ = ["df_roundtrip_trade", "roundtrip_trades"]
