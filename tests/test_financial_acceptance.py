"""Reviewable, implementation-independent financial acceptance cases."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence

import numpy as np
import pytest

from gambit.account import Account
from gambit.backtest_result import BacktestResult
from gambit.holiday_calendars import Calendar
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Order, Trade
from gambit.risk import MaxPositionQuantity
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator

CORPUS_PATH = Path(__file__).parent / "acceptance" / "financial_cases.json"
pytestmark = pytest.mark.acceptance


def corpus() -> dict[str, Any]:
    data = json.loads(CORPUS_PATH.read_text())
    assert data["schema_version"] == 1
    return data


@pytest.mark.parametrize("case", corpus()["accounting_cases"], ids=lambda case: case["name"])
def test_accounting_ledgers_match_manual_acceptance_cases(case: dict[str, Any]) -> None:
    tolerance = corpus()["currency_absolute_tolerance"]
    group = ContractGroup.get(f"acceptance-{case['name']}")
    contract = Contract.create(case["name"], group, multiplier=case["multiplier"])
    timestamps = np.asarray(case["timestamps"], dtype="datetime64[ns]")
    context = SimpleNamespace(marks=np.asarray(case["marks"], dtype=float))

    def mark_price(_contract: Contract, _timestamps: np.ndarray, index: int, state: SimpleNamespace) -> float:
        return float(state.marks[index])

    account = Account(
        [group], timestamps, mark_price, context, starting_equity=case["starting_equity"]
    )
    trades = []
    for timestamp, terms in zip(timestamps, case["trades"]):
        order = MarketOrder(contract=contract, timestamp=timestamp, qty=terms["qty"])
        trades.append(
            Trade(
                contract=contract,
                order=order,
                timestamp=timestamp,
                qty=terms["qty"],
                price=terms["price"],
                fee=terms["fee"],
                commission=terms["commission"],
            )
        )

    account.add_trades(trades)
    account.calc(timestamps[-1])
    actual = account.symbol_pnls[contract.symbol].df()
    expected = case["expected"]

    assert actual["timestamp"].to_numpy().astype("datetime64[ns]").tolist() == timestamps.tolist()
    assert actual["position"].to_list() == expected["position"]
    for column in ("realized", "unrealized", "fee", "commission", "net_pnl"):
        np.testing.assert_allclose(
            actual[column].to_numpy(), expected[column], rtol=0.0, atol=tolerance
        )
    observed_equity = case["starting_equity"] + actual["net_pnl"].to_numpy()
    np.testing.assert_allclose(observed_equity, expected["equity"], rtol=0.0, atol=tolerance)
    assert account.equity(timestamps[-1]) == pytest.approx(expected["equity"][-1], abs=tolerance)


def test_integrated_strategy_matches_manual_case_and_persists_exactly(tmp_path: Path) -> None:
    data = corpus()
    case = data["integrated_strategy_case"]
    expected = case["expected"]
    tolerance = data["currency_absolute_tolerance"]
    group = ContractGroup.get(f"acceptance-{case['name']}")
    contract = Contract.create(case["name"], group, multiplier=case["multiplier"])
    timestamps = np.asarray(case["timestamps"], dtype="datetime64[ns]")
    proposals = np.asarray(case["proposals"], dtype=int)
    context = SimpleNamespace(prices=np.asarray(case["prices"], dtype=float))

    def price(_contract: Contract, _timestamps: np.ndarray, index: int, state: SimpleNamespace) -> float:
        return float(state.prices[index])

    def signal(*_args: object) -> np.ndarray:
        return proposals != 0

    def rule(
        _group: ContractGroup,
        index: int,
        rule_timestamps: np.ndarray,
        _indicators: SimpleNamespace,
        _signal: np.ndarray,
        _account: Account,
        _orders: Sequence[Order],
        _context: SimpleNamespace,
    ) -> list[Order]:
        return [
            MarketOrder(
                contract=contract,
                timestamp=rule_timestamps[index],
                qty=int(proposals[index]),
                reason_code="ACCEPTANCE_REBALANCE",
            )
        ]

    strategy = Strategy(
        timestamps,
        [group],
        price,
        trade_lag=case["trade_lag"],
        starting_equity=case["starting_equity"],
        strategy_context=context,
    )
    strategy.add_signal("rebalance", signal)
    strategy.add_rule("rebalance", rule, signal_name="rebalance")
    strategy.add_risk_policy(MaxPositionQuantity(case["maximum_position"]))
    strategy.add_market_sim(
        SimpleMarketSimulator(price, commission=case["commission_per_unit"])
    )

    result = strategy.run()
    trades = strategy.trades()
    decisions = strategy.order_decisions
    orders = strategy.orders()

    assert [str(trade.order.timestamp.astype("datetime64[m]")) for trade in trades] == expected["submitted_at"]
    assert [str(trade.timestamp.astype("datetime64[m]")) for trade in trades] == expected["filled_at"]
    assert [trade.qty for trade in trades] == expected["trade_qty"]
    np.testing.assert_allclose(
        [trade.price for trade in trades], expected["trade_price"], rtol=0.0, atol=tolerance
    )
    np.testing.assert_allclose(
        [trade.commission for trade in trades],
        expected["trade_commission"],
        rtol=0.0,
        atol=tolerance,
    )
    assert [order.status.name.lower() for order in orders] == expected["order_status"]
    assert [decision.status.value for decision in decisions] == expected["decision_status"]
    assert [decision.code for decision in decisions] == expected["decision_code"]
    assert strategy.account.position(group, timestamps[-1]) == expected["final_position"]

    final = strategy.df_pnl(group).row(-1, named=True)
    for field in (
        "realized",
        "unrealized",
        "fee",
        "commission",
        "net_pnl",
        "equity",
    ):
        assert final[field] == pytest.approx(expected[f"final_{field}"], abs=tolerance)

    restored = BacktestResult.load(result.save(tmp_path / "financial-acceptance.gambit"))
    assert restored.provenance.snapshot() == result.provenance.snapshot()
    assert restored.telemetry == result.telemetry
    assert restored.frames.keys() == result.frames.keys()
    for name, frame in result.frames.items():
        assert restored.frames[name].equals(frame), name


def test_calendar_matches_manual_independence_day_case() -> None:
    case = corpus()["calendar_case"]
    calendar = Calendar(case["calendar"])

    for observation in case["observations"]:
        assert calendar.is_trading_day(observation["date"]) is observation["is_trading_day"]

    range_case = case["range"]
    actual = calendar.get_trading_days(
        range_case["start"],
        range_case["end"],
        include_first=range_case["include_first"],
        include_last=range_case["include_last"],
    )
    assert actual.astype(str).tolist() == range_case["expected"]

    for offset in case["offsets"]:
        actual_date = calendar.add_trading_days(
            offset["start"], offset["days"], roll=offset["roll"]
        )
        assert str(actual_date.astype("datetime64[D]")) == offset["expected"]
