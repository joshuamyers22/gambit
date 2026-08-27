"""Composable pre-trade risk policies and auditable order decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

import numpy as np

from gambit.account import Account
from gambit.pq_types import Order


class DecisionStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RiskContext:
    account: Account
    timestamp: np.datetime64
    open_orders: Sequence[Order]

    def projected_position(self, order: Order) -> float:
        positions = self.account.positions(order.contract.contract_group, self.timestamp)
        current = sum(qty for contract, qty in positions if contract.symbol == order.contract.symbol)
        pending = sum(
            pending_order.qty
            for pending_order in self.open_orders
            if pending_order.is_open() and pending_order.contract.symbol == order.contract.symbol
        )
        return current + pending + order.qty


@dataclass(frozen=True)
class PolicyResult:
    accepted: bool
    code: str = "accepted"
    message: str = ""


class RiskPolicy(Protocol):
    @property
    def name(self) -> str: ...

    def evaluate(self, order: Order, context: RiskContext) -> PolicyResult: ...


@dataclass(frozen=True)
class OrderDecision:
    order: Order
    status: DecisionStatus
    policy: str
    code: str
    message: str
    proposed_qty: float
    timestamp: np.datetime64


@dataclass(frozen=True)
class MaxOrderQuantity:
    maximum: float
    name: str = "max_order_quantity"

    def __post_init__(self) -> None:
        if not math.isfinite(self.maximum) or self.maximum <= 0:
            raise ValueError("maximum order quantity must be finite and positive")

    def evaluate(self, order: Order, context: RiskContext) -> PolicyResult:
        del context
        if abs(order.qty) > self.maximum:
            return PolicyResult(
                False,
                "order_quantity_exceeded",
                f"absolute order quantity {abs(order.qty):g} exceeds {self.maximum:g}",
            )
        return PolicyResult(True)


@dataclass(frozen=True)
class MaxPositionQuantity:
    maximum: float
    name: str = "max_position_quantity"

    def __post_init__(self) -> None:
        if not math.isfinite(self.maximum) or self.maximum <= 0:
            raise ValueError("maximum position quantity must be finite and positive")

    def evaluate(self, order: Order, context: RiskContext) -> PolicyResult:
        projected = context.projected_position(order)
        position_before_order = projected - order.qty
        within_limit = abs(projected) <= self.maximum
        reduces_existing_breach = abs(projected) < abs(position_before_order)
        if not within_limit and not reduces_existing_breach:
            return PolicyResult(
                False,
                "position_quantity_exceeded",
                f"projected position {projected:g} exceeds {self.maximum:g}",
            )
        return PolicyResult(True)


def decide_order(order: Order, context: RiskContext, policies: Sequence[RiskPolicy]) -> OrderDecision:
    for policy in policies:
        result = policy.evaluate(order, context)
        if not result.accepted:
            return OrderDecision(
                order,
                DecisionStatus.REJECTED,
                policy.name,
                result.code,
                result.message,
                order.qty,
                context.timestamp,
            )
    return OrderDecision(
        order,
        DecisionStatus.ACCEPTED,
        "",
        "accepted",
        "",
        order.qty,
        context.timestamp,
    )
