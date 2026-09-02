"""Detached snapshots for mutable execution-domain objects."""

from __future__ import annotations

import copy

from gambit.pq_types import Order, Trade


def snapshot_order(order: Order) -> Order:
    """Detach mutable order state while retaining canonical contract identity."""
    snapshot = copy.copy(order)
    snapshot.properties = copy.deepcopy(order.properties)
    return snapshot


def snapshot_trade(trade: Trade) -> Trade:
    """Detach mutable trade fields, properties, and originating order state."""
    snapshot = copy.copy(trade)
    snapshot.order = snapshot_order(trade.order)
    snapshot.properties = copy.deepcopy(trade.properties)
    return snapshot


__all__ = ["snapshot_order", "snapshot_trade"]
