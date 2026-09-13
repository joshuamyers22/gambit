"""Seeded state-machine reconciliation against an independent FIFO model."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import numpy as np
import pytest

from gambit.pq_types import Contract, ContractGroup, MarketOrder, Order, OrderStatus, TimeInForce, Trade
from gambit.risk import DecisionStatus
from gambit.strategy import Strategy

CONFIG_PATH = Path(__file__).parent / "acceptance" / "stateful_reconciliation.json"
pytestmark = pytest.mark.acceptance


def configuration() -> dict[str, Any]:
    data = json.loads(CONFIG_PATH.read_text())
    assert data["schema_version"] == 1
    return data


@dataclass(frozen=True)
class OrderRecord:
    order: MarketOrder
    original_qty: int
    identifier: int


@dataclass(frozen=True)
class FillRecord:
    order_identifier: int
    symbol: str
    timestamp: np.datetime64
    qty: int
    price: float
    fee: float
    commission: float


class ReferenceLedger:
    """Small test-only FIFO state machine with no Gambit accounting calls."""

    def __init__(self, multiplier: float) -> None:
        self.multiplier = multiplier
        self.lots: deque[tuple[int, float]] = deque()
        self.realized = 0.0
        self.fee = 0.0
        self.commission = 0.0

    def apply(self, fill: FillRecord) -> None:
        remaining = fill.qty
        while remaining and self.lots and np.sign(self.lots[0][0]) != np.sign(remaining):
            lot_qty, lot_price = self.lots.popleft()
            matched = min(abs(lot_qty), abs(remaining))
            lot_sign = 1 if lot_qty > 0 else -1
            fill_sign = 1 if remaining > 0 else -1
            self.realized += matched * lot_sign * (fill.price - lot_price) * self.multiplier
            lot_qty -= lot_sign * matched
            remaining -= fill_sign * matched
            if lot_qty:
                self.lots.appendleft((lot_qty, lot_price))
        if remaining:
            self.lots.append((remaining, fill.price))
        self.fee += fill.fee
        self.commission += fill.commission

    def values(self, mark: float) -> tuple[int, float, float, float, float, float]:
        position = sum(qty for qty, _price in self.lots)
        unrealized = sum(
            qty * (mark - price) * self.multiplier for qty, price in self.lots
        )
        net_pnl = self.realized + unrealized - self.fee - self.commission
        return position, self.realized, unrealized, self.fee, self.commission, net_pnl


@pytest.mark.parametrize(
    "run_case",
    configuration()["runs"],
    ids=lambda case: f"seed-{case['seed']}-lag-{case['trade_lag']}",
)
def test_seeded_order_trade_and_account_state_reconciles(run_case: dict[str, int]) -> None:
    config = configuration()
    seed = run_case["seed"]
    trade_lag = run_case["trade_lag"]
    steps = config["steps_per_run"]
    tolerance = config["currency_absolute_tolerance"]
    plan_rng = np.random.default_rng(seed)
    fill_rng = np.random.default_rng(seed ^ 0x5EED5EED)
    timestamps = np.datetime64("2024-08-01T09:30", "ns") + np.arange(steps) * np.timedelta64(1, "m")
    group = ContractGroup.get(f"stateful-acceptance-{seed}-{trade_lag}")
    contracts = [
        Contract.create(f"STATEFUL-{seed}-{index}", group, multiplier=multiplier)
        for index, multiplier in enumerate(config["contract_multipliers"])
    ]
    marks = {
        contract.symbol: (
            100.0
            + 25.0 * index
            + np.cumsum(plan_rng.integers(-3, 4, size=steps)).astype(float)
        )
        for index, contract in enumerate(contracts)
    }
    proposal_plan: list[list[tuple[int, int]]] = []
    for _step in range(steps):
        count = int(plan_rng.integers(0, config["maximum_orders_per_step"] + 1))
        proposals = []
        for _proposal in range(count):
            contract_index = int(plan_rng.integers(0, len(contracts)))
            magnitude = int(plan_rng.integers(1, config["maximum_order_quantity"] + 1))
            sign = -1 if int(plan_rng.integers(0, 2)) == 0 else 1
            proposals.append((contract_index, sign * magnitude))
        proposal_plan.append(proposals)

    def mark_price(
        contract: Contract,
        _timestamps: np.ndarray,
        index: int,
        _context: SimpleNamespace,
    ) -> float:
        return float(marks[contract.symbol][index])

    order_records: list[OrderRecord] = []
    fills: list[FillRecord] = []
    cancelled_identifiers: set[int] = set()
    partially_filled_identifiers: set[int] = set()
    next_order_identifier = 0
    next_fill_identifier = 0

    def signal(*_args: object) -> np.ndarray:
        return np.ones(steps, dtype=bool)

    def rule(
        _group: ContractGroup,
        index: int,
        rule_timestamps: np.ndarray,
        *_args: object,
    ) -> list[Order]:
        nonlocal next_order_identifier
        orders: list[Order] = []
        for contract_index, quantity in proposal_plan[index]:
            identifier = next_order_identifier
            next_order_identifier += 1
            order = MarketOrder(
                contract=contracts[contract_index],
                timestamp=rule_timestamps[index],
                qty=quantity,
                time_in_force=TimeInForce.GTC,
                reason_code="SEEDED_ACCEPTANCE",
                properties=SimpleNamespace(acceptance_order_id=identifier),
            )
            order_records.append(OrderRecord(order, quantity, identifier))
            orders.append(order)
        return orders

    def simulator(
        orders: Sequence[Order],
        index: int,
        simulator_timestamps: np.ndarray,
        *_args: object,
    ) -> list[Trade]:
        nonlocal next_fill_identifier
        trades = []
        for order in orders:
            action = int(fill_rng.integers(0, 100))
            identifier = int(order.properties.acceptance_order_id)
            if action < config["cancel_percent"]:
                order.cancel()
                cancelled_identifiers.add(identifier)
                continue
            if action >= config["cancel_percent"] + config["fill_percent"]:
                continue
            magnitude = int(fill_rng.integers(1, abs(order.qty) + 1))
            quantity = magnitude if order.qty > 0 else -magnitude
            if magnitude < abs(order.qty):
                partially_filled_identifiers.add(identifier)
            price = mark_price(order.contract, simulator_timestamps, index, SimpleNamespace())
            price += 0.25 * ((identifier % 3) - 1)
            commission = magnitude * config["commission_per_unit"]
            fee_sign = -1.0 if (identifier + index) % 11 == 0 else 1.0
            fee = fee_sign * magnitude * config["fee_per_unit"]
            fill = FillRecord(
                identifier,
                order.contract.symbol,
                simulator_timestamps[index],
                quantity,
                price,
                fee,
                commission,
            )
            fills.append(fill)
            trades.append(
                Trade(
                    order.contract,
                    order,
                    simulator_timestamps[index],
                    quantity,
                    price,
                    fee=fee,
                    commission=commission,
                    properties=SimpleNamespace(acceptance_fill_id=next_fill_identifier),
                )
            )
            next_fill_identifier += 1
        return trades

    strategy = Strategy(
        timestamps,
        [group],
        mark_price,
        trade_lag=trade_lag,
        starting_equity=config["starting_equity"],
        strategy_context=SimpleNamespace(),
        log_orders=False,
        log_trades=False,
    )
    strategy.add_signal("seeded", signal)
    strategy.add_rule("seeded", rule, signal_name="seeded")
    strategy.add_market_sim(simulator)
    result = strategy.run()

    context = f"seed={seed}, trade_lag={trade_lag}"
    actual_trades = strategy.trades()
    assert strategy.account.trade_count == len(fills), context
    assert len(actual_trades) == len(fills), context
    assert [
        (
            trade.order.properties.acceptance_order_id,
            trade.contract.symbol,
            trade.timestamp,
            trade.qty,
            trade.price,
            trade.fee,
            trade.commission,
        )
        for trade in actual_trades
    ] == [
        (
            fill.order_identifier,
            fill.symbol,
            fill.timestamp,
            fill.qty,
            fill.price,
            fill.fee,
            fill.commission,
        )
        for fill in fills
    ], context

    fills_by_order: dict[int, int] = defaultdict(int)
    for fill in fills:
        fills_by_order[fill.order_identifier] += fill.qty
    expected_statuses = []
    for record in order_records:
        expected_remaining = record.original_qty - fills_by_order[record.identifier]
        if record.identifier in cancelled_identifiers:
            expected_status = OrderStatus.CANCELLED
        elif expected_remaining == 0:
            expected_status = OrderStatus.FILLED
        elif expected_remaining == record.original_qty:
            expected_status = OrderStatus.OPEN
        else:
            expected_status = OrderStatus.PARTIALLY_FILLED
        expected_statuses.append(expected_status)
        assert record.order.qty == expected_remaining, f"{context}, order={record.identifier}"
        assert record.order.status is expected_status, f"{context}, order={record.identifier}"

    reported_orders = strategy.orders()
    assert [order.properties.acceptance_order_id for order in reported_orders] == [
        record.identifier for record in order_records
    ], context
    assert [order.qty for order in reported_orders] == [
        record.order.qty for record in order_records
    ], context
    assert [order.status for order in reported_orders] == expected_statuses, context
    assert len(strategy.order_decisions) == len(order_records), context
    assert all(
        decision.status is DecisionStatus.ACCEPTED for decision in strategy.order_decisions
    ), context
    assert [int(decision.proposed_qty) for decision in strategy.order_decisions] == [
        record.original_qty for record in order_records
    ], context
    assert [int(decision.snapshot.qty) for decision in strategy.order_decisions] == [
        record.original_qty for record in order_records
    ], context

    ledgers = {
        contract.symbol: ReferenceLedger(contract.multiplier) for contract in contracts
    }
    fills_by_timestamp: dict[np.datetime64, list[FillRecord]] = defaultdict(list)
    for fill in fills:
        fills_by_timestamp[fill.timestamp].append(fill)
    for index, timestamp in enumerate(timestamps):
        step_context = f"{context}, step={index}, timestamp={timestamp}"
        for fill in fills_by_timestamp[timestamp]:
            ledgers[fill.symbol].apply(fill)
        strategy.account.calc(timestamp)
        expected_account_net = 0.0
        expected_group_position = 0
        for contract in contracts:
            expected = ledgers[contract.symbol].values(marks[contract.symbol][index])
            expected_group_position += expected[0]
            expected_account_net += expected[-1]
            if contract.symbol not in strategy.account.symbol_pnls:
                assert expected == (0, 0.0, 0, 0.0, 0.0, 0.0), step_context
                continue
            actual = strategy.account.symbol_pnls[contract.symbol].pnl(timestamp)
            assert actual[0] == expected[0], step_context
            np.testing.assert_allclose(
                actual[2:], expected[1:], rtol=0.0, atol=tolerance, err_msg=step_context
            )
        assert strategy.account.position(group, timestamp) == expected_group_position, step_context
        assert strategy.account.equity(timestamp) == pytest.approx(
            config["starting_equity"] + expected_account_net,
            rel=0.0,
            abs=tolerance,
        ), step_context

    assert len(fills) >= steps // 2, context
    assert cancelled_identifiers, context
    assert partially_filled_identifiers, context
    assert result.telemetry.orders_proposed == len(order_records), context
    assert result.telemetry.orders_accepted == len(order_records), context
    assert result.telemetry.trades_executed == len(fills), context
