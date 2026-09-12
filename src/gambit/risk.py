"""Composable pre-trade risk policies and auditable order decisions."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol, Sequence

import numpy as np

from gambit.account import Account
from gambit.instruments import Tradability
from gambit.order_callback_state import OrderCallbackState
from gambit.pq_types import LimitOrder, Order, RollOrder, StopLimitOrder, VWAPOrder, _whole_quantity


class DecisionStatus(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RiskContext:
    account: Account
    timestamp: np.datetime64
    open_orders: Sequence[Order]

    def projected_position(self, order: Order) -> float:
        """Net position assuming all pending orders and the proposal fill."""
        positions = self.account.positions(order.contract.contract_group, self.timestamp)
        current = sum(qty for contract, qty in positions if contract.symbol == order.contract.symbol)
        pending = sum(
            pending_order.qty
            for pending_order in self.open_orders
            if pending_order.is_open() and pending_order.contract.symbol == order.contract.symbol
        )
        return current + pending + order.qty

    def position_bounds(self, order: Order) -> tuple[float, float]:
        """Reachable short/long endpoints for independently fillable orders.

        Includes no fill through full fill of each remaining quantity and the
        proposal. Cancellation requests reserve exposure until acknowledged.
        """
        positions = self.account.positions(order.contract.contract_group, self.timestamp)
        current = sum(qty for contract, qty in positions if contract.symbol == order.contract.symbol)
        lower = current + min(0, order.qty)
        upper = current + max(0, order.qty)
        for pending in self.open_orders:
            if pending.is_open() and pending.contract.symbol == order.contract.symbol:
                lower += min(0, pending.qty)
                upper += max(0, pending.qty)
        return lower, upper


@dataclass(frozen=True)
class PolicyResult:
    accepted: bool
    code: str = "accepted"
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise TypeError("policy result accepted must be a bool")
        if not isinstance(self.code, str) or not self.code:
            raise ValueError("policy result code must be a non-empty string")
        if not isinstance(self.message, str):
            raise TypeError("policy result message must be a string")


class RiskPolicy(Protocol):
    @property
    def name(self) -> str: ...

    def evaluate(self, order: Order, context: RiskContext) -> PolicyResult: ...


@dataclass(frozen=True)
class OrderSnapshot:
    """Detached decision-time fields for built-in order types.

    Arbitrary user properties and custom subclass terms are not serialized.
    Roll-leg identity is the only engine-owned property captured here.
    """

    symbol: str
    contract_group: str
    multiplier: float
    expiry: np.datetime64 | None
    submitted_at: np.datetime64
    qty: float
    order_type: str
    time_in_force: str
    order_status: str
    reason_code: str
    limit_price: float | None
    trigger_price: float | None
    triggered: bool | None
    vwap_stop: float | None
    vwap_end_time: np.datetime64 | None
    reopen_symbol: str | None
    reopen_contract_group: str | None
    close_qty: float | None
    reopen_qty: float | None
    roll_id: str | None
    roll_leg: str | None

    @classmethod
    def capture(cls, order: Order) -> OrderSnapshot:
        def roll_property(name: str) -> str | None:
            value = getattr(order.properties, name, None)
            return value if isinstance(value, str) else None

        return cls(
            symbol=order.contract.symbol,
            contract_group=order.contract.contract_group.name,
            multiplier=order.contract.multiplier,
            expiry=order.contract.expiry,
            submitted_at=order.timestamp,
            qty=order.qty,
            order_type=type(order).__name__,
            time_in_force=order.time_in_force.name,
            order_status=order.status.name.lower(),
            reason_code=order.reason_code,
            limit_price=order.limit_price if isinstance(order, (LimitOrder, StopLimitOrder)) else None,
            trigger_price=order.trigger_price if isinstance(order, StopLimitOrder) else None,
            triggered=order.triggered if isinstance(order, StopLimitOrder) else None,
            vwap_stop=order.vwap_stop if isinstance(order, VWAPOrder) else None,
            vwap_end_time=order.vwap_end_time if isinstance(order, VWAPOrder) else None,
            reopen_symbol=order.reopen_contract.symbol if isinstance(order, RollOrder) else None,
            reopen_contract_group=order.reopen_contract.contract_group.name if isinstance(order, RollOrder) else None,
            close_qty=order.close_qty if isinstance(order, RollOrder) else None,
            reopen_qty=order.reopen_qty if isinstance(order, RollOrder) else None,
            roll_id=roll_property("_gambit_roll_id"),
            roll_leg=roll_property("_gambit_roll_leg"),
        )


@dataclass(frozen=True)
class OrderDecision:
    """Policy outcome with an immutable snapshot and a live compatibility order.

    Use ``snapshot`` for audit values; ``order`` follows execution lifecycle.
    """

    order: Order
    status: DecisionStatus
    policy: str
    code: str
    message: str
    proposed_qty: float
    timestamp: np.datetime64
    snapshot: OrderSnapshot = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "snapshot", OrderSnapshot.capture(self.order))


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
    """Cap each independently reachable side, permitting breach reduction.

    An order must leave the endpoint it extends within the cap. The other
    endpoint is unchanged, so an existing breach on that side does not prevent
    reduction. Opposite pending orders never provide admission credit.
    """
    maximum: float
    name: str = "max_position_quantity"

    def __post_init__(self) -> None:
        if not math.isfinite(self.maximum) or self.maximum <= 0:
            raise ValueError("maximum position quantity must be finite and positive")

    def evaluate(self, order: Order, context: RiskContext) -> PolicyResult:
        lower, upper = context.position_bounds(order)
        within_limit = upper <= self.maximum if order.qty > 0 else lower >= -self.maximum
        if not within_limit:
            return PolicyResult(
                False,
                "position_quantity_exceeded",
                f"reachable position range [{lower:g}, {upper:g}] extends beyond {self.maximum:g}",
            )
        return PolicyResult(True)


@dataclass(frozen=True)
class MaxVolumeParticipation:
    """Reject orders exceeding a fraction of externally supplied market volume."""

    maximum_fraction: float
    volume: Callable[[Order, np.datetime64], float]
    name: str = "max_volume_participation"

    def __post_init__(self) -> None:
        if not math.isfinite(self.maximum_fraction) or not 0 < self.maximum_fraction <= 1:
            raise ValueError("maximum volume participation must be in (0, 1]")

    def evaluate(self, order: Order, context: RiskContext) -> PolicyResult:
        available = self.volume(order, context.timestamp)
        if not math.isfinite(available) or available <= 0:
            return PolicyResult(False, "volume_unavailable", "available volume must be finite and positive")
        participation = abs(order.qty) / available
        if participation > self.maximum_fraction:
            return PolicyResult(
                False,
                "volume_participation_exceeded",
                f"participation {participation:.6g} exceeds {self.maximum_fraction:.6g}",
            )
        return PolicyResult(True)


@dataclass(frozen=True)
class InstrumentTradabilityPolicy:
    """Enforce instrument state and expiry before an order reaches execution."""

    allow_risk_reducing: bool = True
    name: str = "instrument_tradability"

    def evaluate(self, order: Order, context: RiskContext) -> PolicyResult:
        spec = order.contract.instrument_spec
        if order.contract.expiry is not None and context.timestamp > order.contract.expiry:
            return PolicyResult(False, "contract_expired", f"{order.contract.symbol} is expired")
        if spec.tradability is Tradability.ACTIVE:
            return PolicyResult(True)

        projected = context.projected_position(order)
        position_before_order = projected - order.qty
        reduces_risk = abs(projected) < abs(position_before_order)
        if self.allow_risk_reducing and reduces_risk and spec.tradability in {
            Tradability.IGNORED,
            Tradability.UNTRADEABLE,
        }:
            return PolicyResult(True)
        return PolicyResult(
            False,
            f"instrument_{spec.tradability.value}",
            f"{order.contract.symbol} is marked {spec.tradability.value}",
        )


def decide_order(order: Order, context: RiskContext, policies: Sequence[RiskPolicy]) -> OrderDecision:
    # Standalone decisions need the same quantity invariants as rule admission,
    # including when no policies run. Pending exposure must be valid as well.
    _whole_quantity(order.qty, field_name="order qty")
    for pending_order in context.open_orders:
        if pending_order.is_open():
            _whole_quantity(pending_order.qty, field_name="pending order qty")
    for policy in policies:
        states = [OrderCallbackState.capture(item) for item in (order, *context.open_orders)]
        try:
            result = policy.evaluate(order, context)
            for state in states:
                state.validate_unchanged()
        except BaseException:  # policy callbacks must not leave orders mutated on failure
            for state in states:
                state.restore()
            raise
        if not isinstance(result, PolicyResult):
            raise TypeError(f"risk policy {policy.name!r} must return a PolicyResult")
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
