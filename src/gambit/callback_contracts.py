"""Pure validation for values returned by user-supplied backtest callbacks."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from gambit.pq_types import ContractGroup, Order, OrderStatus, Trade


def validate_stage_values(result: object, expected_length: int, *, stage: str) -> np.ndarray:
    """Require one detached, immutable value per strategy timestamp."""
    if not isinstance(result, np.ndarray):
        raise TypeError(f"{stage} callback must return a NumPy array or Polars Series")
    if result.ndim != 1:
        raise ValueError(f"{stage} callback must return a one-dimensional array")
    if len(result) != expected_length:
        raise ValueError(
            f"{stage} callback returned {len(result)} values for {expected_length} strategy timestamps"
        )
    values = result.copy()
    values.flags.writeable = False
    return values


def validate_rule_orders(
    result: object,
    contract_group: ContractGroup,
    current_timestamp: object,
) -> list[Order]:
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
        if order.timestamp != current_timestamp:
            raise ValueError("rule order timestamp does not match the current strategy timestamp")
    return orders


def validate_market_trades(
    result: object,
    open_orders: Sequence[Order],
    current_timestamp: object,
    original_states: dict[int, tuple[float, OrderStatus]],
) -> list[Trade]:
    """Validate one market-simulator result without mutating account state."""
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
        raise TypeError("market simulator must return a sequence of Trade objects")

    trades = list(result)
    for trade in trades:
        if not isinstance(trade, Trade):
            raise TypeError(f"market simulator returned a non-Trade value: {trade!r}")
        if not any(trade.order is order for order in open_orders):
            raise ValueError("market simulator returned a trade for an order outside the open order set")
        if trade.contract is not trade.order.contract:
            raise ValueError("market simulator trade contract does not match its order")
        if trade.timestamp != current_timestamp:
            raise ValueError("market simulator trade timestamp does not match the current strategy timestamp")
    for order in open_orders:
        filled_quantity = sum(trade.qty for trade in trades if trade.order is order)
        original_quantity, original_status = original_states[id(order)]
        expected_remaining = original_quantity - filled_quantity
        simulator_did_not_apply_fill = order.qty == original_quantity and order.status is original_status
        expected_fill_status = OrderStatus.FILLED if expected_remaining == 0 else OrderStatus.PARTIALLY_FILLED
        simulator_applied_fill = filled_quantity != 0 and order.qty == expected_remaining and order.status in (
            expected_fill_status,
            OrderStatus.CANCELLED,
        )
        simulator_cancelled_order = (
            filled_quantity == 0 and order.qty == original_quantity and order.status is OrderStatus.CANCELLED
        )
        if not simulator_did_not_apply_fill and not simulator_applied_fill and not simulator_cancelled_order:
            raise ValueError("market simulator trades do not match the originating order quantity changes")
    return trades


__all__ = ["validate_market_trades", "validate_rule_orders", "validate_stage_values"]
