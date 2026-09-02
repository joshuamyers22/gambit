"""Pure validation for values returned by user-supplied backtest callbacks."""

from __future__ import annotations

from collections.abc import Sequence

from gambit.pq_types import ContractGroup, Order, Trade


def validate_rule_orders(result: object, contract_group: ContractGroup) -> list[Order]:
    """Validate and normalize one rule callback result."""
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
        raise TypeError("rule callback must return a sequence of Order objects")

    orders = list(result)
    for order in orders:
        if not isinstance(order, Order):
            raise TypeError(f"rule callback returned a non-Order value: {order!r}")
        if order.contract.contract_group is not contract_group:
            raise ValueError(f"rule returned {order.contract.symbol} outside contract group {contract_group.name}")
        registered = contract_group.contracts.get(order.contract.symbol)
        if registered is not order.contract:
            raise ValueError(f"rule returned an unregistered contract: {order.contract.symbol}")
    return orders


def validate_market_trades(
    result: object,
    open_orders: Sequence[Order],
    current_timestamp: object,
) -> Sequence[Trade]:
    """Validate one market-simulator result without mutating account state."""
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
        raise TypeError("market simulator must return a sequence of Trade objects")

    for trade in result:
        if not isinstance(trade, Trade):
            raise TypeError(f"market simulator returned a non-Trade value: {trade!r}")
        if not any(trade.order is order for order in open_orders):
            raise ValueError("market simulator returned a trade for an order outside the open order set")
        if trade.contract is not trade.order.contract:
            raise ValueError("market simulator trade contract does not match its order")
        if trade.timestamp != current_timestamp:
            raise ValueError("market simulator trade timestamp does not match the current strategy timestamp")
    return result


__all__ = ["validate_market_trades", "validate_rule_orders"]
