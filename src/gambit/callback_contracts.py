"""Pure validation for values returned by user-supplied backtest callbacks."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from gambit.pq_types import (
    ContractGroup,
    LimitOrder,
    Order,
    OrderStatus,
    RollOrder,
    StopLimitOrder,
    Trade,
    VWAPOrder,
    _finite_real,
    _validate_order_references,
    _validate_trade_references,
    _validated_trade_numbers,
    _whole_quantity,
)


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
    *,
    pending_orders: Sequence[Order] = (),
    roll_id_prefix: str | None = None,
) -> list[Order]:
    """Validate and normalize one rule callback result."""
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes)):
        raise TypeError("rule callback must return a sequence of Order objects")

    submitted = list(result)
    seen_order_ids = {id(order) for order in pending_orders}
    orders: list[Order] = []
    for submission_index, order in enumerate(submitted):
        if not isinstance(order, Order):
            raise TypeError(f"rule callback returned a non-Order value: {order!r}")
        if id(order) in seen_order_ids:
            raise ValueError("rule returned a duplicate or already-pending order object")
        seen_order_ids.add(id(order))
        if isinstance(order, StopLimitOrder):
            raise ValueError(
                "StopLimitOrder is deprecated and cannot be executed; emit a MarketOrder or "
                "LimitOrder from an explicit trigger rule"
            )
        # Constructors are not a trust boundary: a rule can mutate these fields
        # before returning. Roll quantities are checked during leg expansion.
        _validate_order_references(order)
        if not isinstance(order, RollOrder):
            _whole_quantity(order.qty, field_name="order qty")
        if isinstance(order, LimitOrder):
            _finite_real(order.limit_price, field_name="limit price")
        if order.contract.contract_group is not contract_group:
            raise ValueError(f"rule returned {order.contract.symbol} outside contract group {contract_group.name}")
        registered = contract_group.contracts.get(order.contract.symbol)
        if registered is not order.contract:
            raise ValueError(f"rule returned an unregistered contract: {order.contract.symbol}")
        if np.isnat(order.timestamp) or order.timestamp != current_timestamp:
            raise ValueError("rule order timestamp does not match the current strategy timestamp")
        if isinstance(order, VWAPOrder):
            order._validate_window()
            order._validate_stop()
        if isinstance(order, RollOrder):
            reopen_registered = contract_group.contracts.get(order.reopen_contract.symbol)
            if reopen_registered is not order.reopen_contract:
                raise ValueError(f"rule returned an unregistered roll contract: {order.reopen_contract.symbol}")
            legs = order.legs()
            if roll_id_prefix is not None:
                # Strategy supplies a stable batch ordinal. Assign IDs only to
                # fresh expanded legs, never mutate the source roll command.
                for leg in legs:
                    leg.properties._gambit_roll_id = f"{roll_id_prefix}:{submission_index}"
            orders.extend(legs)
        else:
            orders.append(order)
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
    open_order_ids = {id(order) for order in open_orders}
    filled_quantities: dict[int, int] = {}
    for trade in trades:
        if not isinstance(trade, Trade):
            raise TypeError(f"market simulator returned a non-Trade value: {trade!r}")
        _validate_trade_references(trade.contract, trade.order, trade.timestamp)
        if id(trade.order) not in open_order_ids:
            raise ValueError("market simulator returned a trade for an order outside the open order set")
        if trade.timestamp != current_timestamp:
            raise ValueError("market simulator trade timestamp does not match the current strategy timestamp")
        # Trade fields and order state are mutable. Validate against the quantity
        # captured before the callback, never its possibly modified remainder.
        quantity, _, _, _ = _validated_trade_numbers(trade.qty, trade.price, trade.fee, trade.commission)
        order_id = id(trade.order)
        original_quantity, _ = original_states[order_id]
        if (quantity > 0) != (original_quantity > 0):
            raise ValueError("market simulator fill has the opposite sign to its originating order")
        filled_quantities[order_id] = filled_quantities.get(order_id, 0) + quantity
        if abs(filled_quantities[order_id]) > abs(original_quantity):
            raise ValueError("market simulator fills exceed the originating order remaining quantity")
    for order in open_orders:
        filled_quantity = filled_quantities.get(id(order), 0)
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
