"""Single-contract position and P&L ledger."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, cast

import numpy as np
import polars as pl
from numpy.typing import NDArray
from sortedcontainers import SortedDict

from gambit.boundaries import timestamp_index, validate_price_value, validate_timestamp_grid
from gambit.pnl_calculation import calculate_trade_pnl
from gambit.pq_types import Contract, Trade


@dataclass(frozen=True)
class ContractPNLState:
    trade_pnl: SortedDict
    net_pnl: SortedDict
    open_qtys: NDArray[np.int_]
    open_prices: NDArray[np.float64]
    first_trade_timestamp: np.datetime64 | None
    final_pnl: float
    new_trades_added: bool


def leading_nan_to_zero(df: pl.DataFrame, columns: Sequence[str]) -> pl.DataFrame:
    for column in columns:
        vals = df[column].to_numpy().copy()
        non_nan_indices: NDArray[np.intp] = np.flatnonzero(~np.isnan(vals))
        first_non_nan_index = int(non_nan_indices[0]) if len(non_nan_indices) else -1

        if first_non_nan_index > 0 and first_non_nan_index < len(vals):
            vals[:first_non_nan_index] = np.nan_to_num(vals[:first_non_nan_index])
            df = df.with_columns(pl.Series(column, vals))
    return df


def find_index_before(sorted_dict: SortedDict, key: Any) -> int:
    """
    Find index of the first key in a sorted dict that is less than or equal to the key passed in.
    If the key is less than the first key in the dict, return -1
    """
    size = len(sorted_dict)
    if not size:
        return -1
    i = sorted_dict.bisect_left(key)
    if i == size:
        return size - 1
    if sorted_dict.keys()[i] != key:
        return i - 1
    return i


class ContractPNL:
    """Computes pnl for a single contract over time given trades and market data
    >>> from gambit.pq_types import MarketOrder
    >>> Contract.clear_cache()
    >>> aapl_contract = Contract.create('AAPL')
    >>> timestamps = np.arange(np.datetime64('2018-01-01'), np.datetime64('2018-01-04'))
    >>> def get_price(contract, timestamps, idx, strategy_context):
    ...    assert contract.symbol == 'AAPL', f'unknown contract: {contract}'
    ...    return idx + 10.1

    >>> contract_pnl = ContractPNL(aapl_contract, timestamps, get_price, SimpleNamespace())
    >>> trade_5 = Trade(aapl_contract, MarketOrder(contract=aapl_contract, timestamp=timestamps[1], qty=20), timestamps[2], 10, 16.2)
    >>> trade_6 = Trade(aapl_contract, MarketOrder(contract=aapl_contract, timestamp=timestamps[1], qty=-20), timestamps[2], -10, 16.5)
    >>> trade_7 = Trade(aapl_contract, MarketOrder(contract=aapl_contract, timestamp=timestamps[1], qty=-20), timestamps[2], -10, 16.5)
    >>> contract_pnl._add_trades([trade_5, trade_6])
    >>> contract_pnl._add_trades([trade_7])
    >>> df = contract_pnl.df()
    >>> assert (len(df == 1))
    >>> row = df.iloc[0]
    >>> assert row.to_dict() == {'symbol': 'AAPL',
    ... 'timestamp': pd.Timestamp('2018-01-03 00:00:00'),
    ... 'position': -10,
    ... 'price': 12.1,
    ... 'unrealized': 44.0,
    ... 'realized': 3.000000000000007,
    ... 'commission': 0.0,
    ... 'fee': 0.0,
    ... 'net_pnl': 47.00000000000001}
    """

    def __init__(
        self,
        contract: Contract,
        account_timestamps: np.ndarray,
        price_function: Callable[[Contract, np.ndarray, int, SimpleNamespace | None], float],
        strategy_context: SimpleNamespace | None,
        *,
        _timestamps_validated: bool = False,
    ) -> None:
        if not _timestamps_validated:
            validate_timestamp_grid(account_timestamps, owner="account")
        self.contract = contract
        self._price_function = price_function
        self.strategy_context = strategy_context
        self._account_timestamps = account_timestamps
        self._trade_pnl = SortedDict()
        self._net_pnl = SortedDict()
        # Store trades that are not offset so when new trades come in we can offset against these to calc pnl
        self.open_qtys: NDArray[np.int_] = np.empty(0, dtype=int)
        self.open_prices: NDArray[np.float64] = np.empty(0, dtype=float)
        self.first_trade_timestamp: np.datetime64 | None = None
        self.final_pnl = np.nan
        self.new_trades_added = False

    def _snapshot_state(self) -> ContractPNLState:
        return ContractPNLState(
            trade_pnl=self._trade_pnl.copy(),
            net_pnl=self._net_pnl.copy(),
            open_qtys=self.open_qtys.copy(),
            open_prices=self.open_prices.copy(),
            first_trade_timestamp=self.first_trade_timestamp,
            final_pnl=self.final_pnl,
            new_trades_added=self.new_trades_added,
        )

    def _restore_state(self, state: ContractPNLState) -> None:
        self._trade_pnl = state.trade_pnl
        self._net_pnl = state.net_pnl
        self.open_qtys = state.open_qtys
        self.open_prices = state.open_prices
        self.first_trade_timestamp = state.first_trade_timestamp
        self.final_pnl = state.final_pnl
        self.new_trades_added = state.new_trades_added

    def _validate_trade_chronology(self, trades: Sequence[Trade]) -> None:
        if not len(trades):
            return
        timestamps = np.unique([trade.timestamp for trade in trades])
        if len(self._trade_pnl):
            prev_max_timestamp, _ = self._trade_pnl.peekitem(-1)
            if timestamps[0] < prev_max_timestamp:
                raise ValueError(
                    "trades can only be added with non-decreasing timestamps "
                    f"for {self.contract.symbol}: current {timestamps[0]}, previous {prev_max_timestamp}"
                )

    def _add_trades(self, trades: Sequence[Trade]) -> None:
        """
        Args:
            trades: Must be sorted by timestamp
        """
        if not len(trades):
            return
        self._validate_trade_chronology(trades)
        timestamps = np.unique([trade.timestamp for trade in trades])
        first_timestamp = timestamps[0]
        for timestamp in list(self._net_pnl.keys()):
            if timestamp >= first_timestamp:
                del self._net_pnl[timestamp]

        if self.first_trade_timestamp is None:
            self.first_trade_timestamp = first_timestamp

        self.new_trades_added = True

        for i, timestamp in enumerate(timestamps):
            t_trades = [trade for trade in trades if trade.timestamp == timestamp]
            open_qtys, open_prices, realized_chg = calculate_trade_pnl(
                self.open_qtys,
                self.open_prices,
                np.array([trade.qty for trade in t_trades], dtype=int),
                np.array([trade.price for trade in t_trades], dtype=float),
                self.contract.multiplier,
            )

            open_qty = int(np.sum(open_qtys))
            if open_qty == 0:
                weighted_avg_price = 0.0
            else:
                weighted_avg_price = np.sum(open_qtys * open_prices) / open_qty

            self.open_qtys = open_qtys
            self.open_prices = open_prices
            position_chg = sum([trade.qty for trade in t_trades])
            commission_chg = sum([trade.commission for trade in t_trades])
            fee_chg = sum([trade.fee for trade in t_trades])
            index = find_index_before(self._trade_pnl, timestamp)
            if index == -1:
                self._trade_pnl[timestamp] = (
                    position_chg,
                    realized_chg,
                    fee_chg,
                    commission_chg,
                    open_qty,
                    weighted_avg_price,
                )
            else:
                prev_timestamp, (prev_position, prev_realized, prev_fee, prev_commission, _, _) = (
                    self._trade_pnl.peekitem(index)
                )
                self._trade_pnl[timestamp] = (
                    prev_position + position_chg,
                    prev_realized + realized_chg,
                    prev_fee + fee_chg,
                    prev_commission + commission_chg,
                    open_qty,
                    weighted_avg_price,
                )
            self.calc_net_pnl(timestamp)

    def calc_net_pnl(self, timestamp: np.datetime64) -> None:
        # If we already calculated unrealized pnl for this timestamp and no new trades were added no need to do anything
        if timestamp in self._net_pnl and not self.new_trades_added:
            return
        if self.first_trade_timestamp is None or timestamp < self.first_trade_timestamp:
            return
        # TODO: Option expiry should be a special case.  If option expires at 3:00 pm, we put in an expiry order at 3 pm and the
        # trade comes in at 3:01 pm.  In this case, the final pnl is recorded at 3:01 but should be at 3 pm.
        if self.contract.expiry is not None and timestamp > self.contract.expiry and not math.isnan(self.final_pnl):
            return

        # make sure timestamp is in the sequence of timestamps we were given
        i = timestamp_index(self._account_timestamps, timestamp, owner="account")

        # Find most current trade PNL, i.e. with the index before or equal to current timestamp.  If not found, set to 0's
        trade_pnl_index = find_index_before(self._trade_pnl, timestamp)
        if trade_pnl_index == -1:
            realized, fee, commission, open_qty, weighted_avg_price = 0, 0, 0, 0, 0
        else:
            _, (_, realized, fee, commission, open_qty, weighted_avg_price) = self._trade_pnl.peekitem(trade_pnl_index)

        price = np.nan

        if math.isclose(open_qty, 0):
            unrealized = 0.0
        else:
            valuation_timestamp = cast(np.datetime64, self._account_timestamps[i])
            price = validate_price_value(
                self._price_function(self.contract, self._account_timestamps, i, self.strategy_context),
                symbol=self.contract.symbol,
                timestamp=valuation_timestamp,
                source="account price callback",
            )

            if math.isnan(price):
                index = find_index_before(self._net_pnl, timestamp)  # Most recent unrealized pnl
                if index == -1:
                    prev_unrealized = 0.0
                else:
                    _, (_, _, prev_unrealized, _) = self._net_pnl.peekitem(index)
                unrealized = prev_unrealized
            else:
                unrealized = open_qty * (price - weighted_avg_price) * self.contract.multiplier

        net_pnl = realized + unrealized - commission - fee

        self._net_pnl[timestamp] = (price, open_qty, unrealized, net_pnl)
        if self.contract.expiry is not None and timestamp > self.contract.expiry:
            self.final_pnl = net_pnl
        self.new_trades_added = False

    def position(self, timestamp: np.datetime64) -> float:
        index = find_index_before(self._trade_pnl, timestamp)
        if index == -1:
            return 0.0
        _, (position, _, _, _, _, _) = self._trade_pnl.peekitem(index)  # Less than or equal to timestamp
        return position

    def net_pnl(self, timestamp: np.datetime64) -> float:
        if self.contract.expiry is not None and timestamp > self.contract.expiry and not math.isnan(self.final_pnl):
            return self.final_pnl
        index = find_index_before(self._net_pnl, timestamp)
        if index == -1:
            return 0.0
        _, (_, _, _, net_pnl) = self._net_pnl.peekitem(index)  # Less than or equal to timestamp
        return net_pnl

    def pnl(self, timestamp: np.datetime64) -> tuple[float, float, float, float, float, float, float]:
        index = find_index_before(self._trade_pnl, timestamp)
        position, realized, fee, commission, price, unrealized, net_pnl = 0, 0, 0, 0, 0, 0, 0
        if index != -1:
            _, (position, realized, fee, commission, _, _) = self._trade_pnl.peekitem(
                index
            )  # Less than or equal to timestamp

        index = find_index_before(self._net_pnl, timestamp)
        if index != -1:
            _, (price, open_position, unrealized, net_pnl) = self._net_pnl.peekitem(
                index
            )  # Less than or equal to timestamp
        return position, price, realized, unrealized, fee, commission, net_pnl

    def df(self) -> pl.DataFrame:
        """Return a Polars DataFrame with P&L data."""
        df_trade_pnl = pl.DataFrame(
            [(k.astype("datetime64[ns]"), v[0], v[1], v[2], v[3]) for k, v in self._trade_pnl.items()],
            schema=["timestamp", "position", "realized", "fee", "commission"],
            orient="row",
        )
        df_net_pnl = pl.DataFrame(
            [(k.astype("datetime64[ns]"), v[0], v[2], v[3]) for k, v in self._net_pnl.items()],
            schema=["timestamp", "price", "unrealized", "net_pnl"],
            orient="row",
        )
        all_timestamps = np.unique(
            np.concatenate((df_trade_pnl["timestamp"].to_numpy(), df_net_pnl["timestamp"].to_numpy()))
        )
        timeline = pl.DataFrame({"timestamp": all_timestamps})
        df_trade_pnl = timeline.join_asof(df_trade_pnl.sort("timestamp"), on="timestamp", strategy="backward")
        df_trade_pnl = leading_nan_to_zero(df_trade_pnl, ["position", "realized", "fee", "commission"])
        df_net_pnl = timeline.join_asof(df_net_pnl.sort("timestamp"), on="timestamp", strategy="backward")
        return (
            df_trade_pnl.join(df_net_pnl, on="timestamp")
            .with_columns(pl.lit(self.contract.symbol).alias("symbol"))
            .select(
                "symbol", "timestamp", "position", "price", "unrealized", "realized", "commission", "fee", "net_pnl"
            )
        )

__all__ = ["ContractPNL", "ContractPNLState", "find_index_before"]
