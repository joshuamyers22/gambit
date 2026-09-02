"""Reusable strategy inputs and price adapters."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import SimpleNamespace
from typing import cast

import numpy as np

from gambit.pq_types import Contract, ContractGroup
from gambit.pq_utils import assert_, np_indexof_sorted
from gambit.strategy_contracts import StrategyContextType


def _timestamp_at(timestamps: np.ndarray, index: int) -> np.datetime64:
    """Return a scalar timestamp from an array with an explicit typing boundary."""
    return cast(np.datetime64, timestamps[index])


@dataclass
class VectorIndicator:
    """
    An indicator created from a vector
    Args:
        vector: Vector with indicator values. Must be the same length as strategy timestamps
    """

    vector: np.ndarray

    def __call__(
        self,
        contract_group: ContractGroup,
        timestamps: np.ndarray,
        indicator_values: SimpleNamespace,
        context: StrategyContextType,
    ) -> np.ndarray:
        return self.vector


@dataclass
class VectorSignal:
    """
    A signal created from a vector that has boolean values
    Args:
        vector: Vector with indicator values. Must be the same length as strategy timestamps
    """

    vector: np.ndarray

    def __call__(
        self,
        contract_group: ContractGroup,
        timestamps: np.ndarray,
        indicator_values: SimpleNamespace,
        parent_values: SimpleNamespace,
        context: StrategyContextType,
    ) -> np.ndarray:
        return self.vector


def get_contract_price_from_dict(
    price_dict: dict[str, dict[np.datetime64, float]], contract: Contract, timestamp: np.datetime64
) -> float:
    assert_(contract.symbol in price_dict, f"{contract.symbol} not found in price_dict")
    ret = price_dict[contract.symbol].get(timestamp)
    if ret is None:
        return math.nan
    return ret


def get_contract_price_from_array_dict(
    price_dict: dict[str, tuple[np.ndarray, np.ndarray]],
    contract: Contract,
    timestamp: np.datetime64,
    allow_previous: bool,
) -> float:
    value = price_dict.get(contract.symbol)
    assert_(value is not None, f"{contract.symbol} not found in price_dict")
    _timestamps, _prices = cast(tuple[np.ndarray, np.ndarray], value)
    idx: int
    if allow_previous:
        idx = int(np.searchsorted(_timestamps, timestamp, side="right")) - 1
    else:
        idx = np_indexof_sorted(_timestamps, timestamp)
    if idx == -1:
        return math.nan
        #     if idx >= len(_prices):
        #         import pdb
        #         pdb.set_trace()
    return float(_prices[idx])


@dataclass
class PriceFuncArrays:
    """
    A function object with a signature of PriceFunctionType. Takes three ndarrays
    of symbols, timestamps and prices
    """

    price_dict: dict[str, tuple[np.ndarray, np.ndarray]]
    allow_previous: bool

    def __init__(
        self, symbols: np.ndarray, timestamps: np.ndarray, prices: np.ndarray, allow_previous: bool = False
    ) -> None:
        assert_(
            len(timestamps) == len(symbols) and len(prices) == len(symbols),
            f"arrays have different sizes: {len(timestamps)} {len(symbols)} {len(prices)}",
        )
        price_dict: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for symbol in np.unique(symbols):
            mask = symbols == symbol
            price_dict[symbol] = (timestamps[mask], prices[mask])
        self.price_dict = price_dict
        self.allow_previous = allow_previous

    def __call__(self, contract: Contract, timestamps: np.ndarray, i: int, context: StrategyContextType) -> float:
        price: float = 0.0
        timestamp = _timestamp_at(timestamps, i)
        if contract.is_basket():
            for _contract, ratio in contract.components:
                price += (
                    get_contract_price_from_array_dict(self.price_dict, _contract, timestamp, self.allow_previous)
                    * ratio
                )
        else:
            price = get_contract_price_from_array_dict(self.price_dict, contract, timestamp, self.allow_previous)
        return price


@dataclass
class PriceFuncArrayDict:
    """
    A function object with a signature of PriceFunctionType and takes a dictionary of
        contract name -> tuple of sorted timestamps and prices

    Args:
        price_dict: a dict with key=contract nane and value a tuple of timestamp and price arrays
        allow_previous: if set and we don't find an exact match for the timestamp, use the
            previous timestamp. Useful if you have a dict with keys containing dates instead of timestamps

    >>> timestamps = np.arange(np.datetime64('2023-01-01'), np.datetime64('2023-01-04'))
    >>> price_dict = {'AAPL': (timestamps, [8, 9, 10]), 'IBM': (timestamps, [20, 21, 22])}
    >>> pricefunc = PriceFuncArrayDict(price_dict)
    >>> Contract.clear_cache()
    >>> aapl = Contract.create('AAPL')
    >>> assert(pricefunc(aapl, timestamps, 2, None) == 10)
    >>> ibm = Contract.create('IBM')
    >>> basket = Contract.create('AAPL_IBM', components=[(aapl, 1), (ibm, -1)])
    >>> assert(pricefunc(basket, timestamps, 1, None) == -12)
    """

    price_dict: dict[str, tuple[np.ndarray, np.ndarray]]
    allow_previous: bool

    def __init__(self, price_dict: dict[str, tuple[np.ndarray, np.ndarray]], allow_previous: bool = False) -> None:
        self.price_dict = price_dict
        self.allow_previous = allow_previous

    def __call__(self, contract: Contract, timestamps: np.ndarray, i: int, context: StrategyContextType) -> float:
        price: float = 0.0
        timestamp = _timestamp_at(timestamps, i)
        if contract.is_basket():
            for _contract, ratio in contract.components:
                price += (
                    get_contract_price_from_array_dict(self.price_dict, _contract, timestamp, self.allow_previous)
                    * ratio
                )
        else:
            price = get_contract_price_from_array_dict(self.price_dict, contract, timestamp, self.allow_previous)
        return price


@dataclass
class PriceFuncDict:
    """
    A function object with a signature of PriceFunctionType and takes a dictionary of contract name -> timestamp -> price
    >>> timestamps = np.arange(np.datetime64('2023-01-01'), np.datetime64('2023-01-04'))
    >>> aapl_prices = [8, 9, 10]
    >>> ibm_prices = [20, 21, 22]
    >>> price_dict = {'AAPL': {}, 'IBM': {}}
    >>> for i, timestamp in enumerate(timestamps):
    ...    price_dict['AAPL'][timestamp] = aapl_prices[i]
    ...    price_dict['IBM'][timestamp] = ibm_prices[i]
    >>> pricefunc = PriceFuncDict(price_dict)
    >>> Contract.clear_cache()
    >>> aapl = Contract.create('AAPL')
    >>> assert(pricefunc(aapl, timestamps, 2, None) == 10)
    >>> ibm = Contract.create('IBM')
    >>> basket = Contract.create('AAPL_IBM', components=[(aapl, 1), (ibm, -1)])
    >>> assert(pricefunc(basket, timestamps, 1, None) == -12)
    """

    price_dict: dict[str, dict[np.datetime64, float]]

    def __init__(self, price_dict: dict[str, dict[np.datetime64, float]]) -> None:
        self.price_dict = price_dict

    def __call__(self, contract: Contract, timestamps: np.ndarray, i: int, context: StrategyContextType) -> float:
        timestamp = _timestamp_at(timestamps, i)
        price: float = 0.0
        if contract.is_basket():
            for _contract, ratio in contract.components:
                price += get_contract_price_from_dict(self.price_dict, _contract, timestamp) * ratio
        else:
            price = get_contract_price_from_dict(self.price_dict, contract, timestamp)
        return price

__all__ = ["PriceFuncArrayDict","PriceFuncArrays","PriceFuncDict","VectorIndicator","VectorSignal","get_contract_price_from_array_dict","get_contract_price_from_dict"]
