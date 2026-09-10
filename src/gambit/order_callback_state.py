"""Scoped guards for engine-owned order fields exposed to callbacks."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from gambit.pq_types import Contract, Order, OrderStatus, TimeInForce


@dataclass(frozen=True)
class OrderCallbackState:
    order: Order
    contract: Contract
    timestamp: np.datetime64
    time_in_force: TimeInForce
    qty: float
    status: OrderStatus

    @classmethod
    def capture(cls, order: Order) -> OrderCallbackState:
        return cls(order, order.contract, order.timestamp, order.time_in_force, order.qty, order.status)

    def validate_identity(self) -> None:
        if getattr(self.order, "contract", None) is not self.contract:
            raise ValueError("callback changed submitted order contract")
        timestamp = getattr(self.order, "timestamp", None)
        if (not isinstance(timestamp, np.datetime64) or timestamp.dtype != self.timestamp.dtype
                or not (timestamp == self.timestamp or (np.isnat(timestamp) and np.isnat(self.timestamp)))):
            raise ValueError("callback changed submitted order timestamp")
        if getattr(self.order, "time_in_force", None) is not self.time_in_force:
            raise ValueError("callback changed submitted order time_in_force")

    def validate_unchanged(self, *, allow_cancel: bool = False) -> None:
        self.validate_identity()
        quantity = getattr(self.order, "qty", None)
        if (isinstance(quantity, (bool, np.bool_))
                or not isinstance(quantity, (int, float, np.integer, np.floating))
                or quantity != self.qty):
            raise ValueError("callback changed protected order quantity")
        status = getattr(self.order, "status", None)
        if status is self.status:
            return
        if (allow_cancel and self.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED,
                                            OrderStatus.CANCEL_REQUESTED)
                and status in (OrderStatus.CANCEL_REQUESTED, OrderStatus.CANCELLED)):
            return
        raise ValueError("callback changed protected order status")

    def restore(self) -> None:
        self.order.contract = self.contract
        self.order.timestamp = self.timestamp
        self.order.time_in_force = self.time_in_force
        self.order.qty = self.qty
        self.order.status = self.status
