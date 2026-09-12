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
from gambit.pq_types import (
    Contract,
    ContractGroup,
    MarketOrder,
    Order,
    RollOrder,
    TimeInForce,
    Trade,
    VWAPOrder,
)
from gambit.risk import MaxPositionQuantity
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator, VWAPMarketSimulator

CORPUS_PATH = Path(__file__).parent / "acceptance" / "financial_cases.json"
pytestmark = pytest.mark.acceptance


def corpus() -> dict[str, Any]:
    data = json.loads(CORPUS_PATH.read_text())
    assert data["schema_version"] == 3
    return data


def numeric_token(value: str | float) -> float:
    if not isinstance(value, str):
        return value
    return {
        "nan": float("nan"),
        "positive_infinity": float("inf"),
        "negative_infinity": float("-inf"),
    }[value]


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


def test_partial_fills_match_manual_lot_and_mark_to_market_case() -> None:
    data = corpus()
    case = data["partial_fill_case"]
    expected = case["expected"]
    tolerance = data["currency_absolute_tolerance"]
    group = ContractGroup.get(f"acceptance-{case['name']}")
    contract = Contract.create(case["name"], group, multiplier=case["multiplier"])
    timestamps = np.asarray(case["timestamps"], dtype="datetime64[ns]")
    context = SimpleNamespace(marks=np.asarray(case["marks"], dtype=float))
    observed_status: list[str] = []
    observed_remaining: list[int] = []

    def price(_contract: Contract, _timestamps: np.ndarray, index: int, state: SimpleNamespace) -> float:
        return float(state.marks[index])

    def signal(*_args: object) -> np.ndarray:
        return np.asarray([True, False, False, False])

    def rule(
        _group: ContractGroup,
        index: int,
        rule_timestamps: np.ndarray,
        *_args: object,
    ) -> list[Order]:
        return [
            MarketOrder(
                contract=contract,
                timestamp=rule_timestamps[index],
                qty=case["order_qty"],
                time_in_force=TimeInForce.GTC,
            )
        ]

    def partial_simulator(
        orders: Sequence[Order], index: int, simulator_timestamps: np.ndarray, *_args: object
    ) -> list[Trade]:
        if not orders:
            return []
        order = orders[0]
        fill_qty = case["fill_qty"][index]
        trade = Trade(
            contract,
            order,
            simulator_timestamps[index],
            fill_qty,
            case["fill_price"][index],
            commission=case["fill_commission"][index],
        )
        order.fill(fill_qty)
        observed_status.append(order.status.name.lower())
        observed_remaining.append(order.qty)
        return [trade]

    strategy = Strategy(
        timestamps,
        [group],
        price,
        trade_lag=0,
        starting_equity=case["starting_equity"],
        strategy_context=context,
        log_trades=False,
    )
    strategy.add_signal("enter", signal)
    strategy.add_rule("enter", rule, signal_name="enter")
    strategy.add_market_sim(partial_simulator)
    strategy.run()

    assert [trade.qty for trade in strategy.trades()] == case["fill_qty"]
    assert [trade.price for trade in strategy.trades()] == case["fill_price"]
    assert observed_status == expected["fill_status"]
    assert observed_remaining == expected["remaining_qty"]
    assert strategy.account.position(group, timestamps[-1]) == expected["final_position"]
    final = strategy.df_pnl(group).row(-1, named=True)
    for field in ("realized", "unrealized", "commission", "net_pnl", "equity"):
        assert final[field] == pytest.approx(expected[f"final_{field}"], abs=tolerance)


def test_roll_matches_manual_per_contract_and_aggregate_ledgers() -> None:
    data = corpus()
    case = data["roll_case"]
    expected = case["expected"]
    tolerance = data["currency_absolute_tolerance"]
    group = ContractGroup.get(f"acceptance-{case['name']}")
    outgoing = Contract.create(
        case["outgoing"]["symbol"], group, multiplier=case["outgoing"]["multiplier"]
    )
    incoming = Contract.create(
        case["incoming"]["symbol"], group, multiplier=case["incoming"]["multiplier"]
    )
    timestamps = np.asarray(case["timestamps"], dtype="datetime64[ns]")
    prices = {
        outgoing.symbol: np.asarray(case["outgoing"]["prices"], dtype=float),
        incoming.symbol: np.asarray(case["incoming"]["prices"], dtype=float),
    }

    def price(contract: Contract, _timestamps: np.ndarray, index: int, _context: object) -> float:
        return float(prices[contract.symbol][index])

    def signal(*_args: object) -> np.ndarray:
        return np.asarray([True, True, False])

    def rule(
        _group: ContractGroup,
        index: int,
        rule_timestamps: np.ndarray,
        *_args: object,
    ) -> list[Order]:
        if index == 0:
            return [
                MarketOrder(
                    contract=outgoing,
                    timestamp=rule_timestamps[index],
                    qty=case["initial_qty"],
                    reason_code="ACCEPTANCE_ENTRY",
                )
            ]
        return [
            RollOrder(
                contract=outgoing,
                reopen_contract=incoming,
                timestamp=rule_timestamps[index],
                close_qty=-case["initial_qty"],
                reopen_qty=case["reopen_qty"],
                reason_code="ACCEPTANCE_ROLL",
            )
        ]

    strategy = Strategy(
        timestamps,
        [group],
        price,
        trade_lag=0,
        starting_equity=case["starting_equity"],
    )
    strategy.add_signal("entry-and-roll", signal)
    strategy.add_rule("entry-and-roll", rule, signal_name="entry-and-roll")
    strategy.add_market_sim(
        SimpleMarketSimulator(price, commission=case["commission_per_unit"])
    )
    strategy.run()

    trades = strategy.trades()
    orders = strategy.orders()
    assert [trade.contract.symbol for trade in trades] == expected["trade_symbol"]
    assert [trade.qty for trade in trades] == expected["trade_qty"]
    assert [trade.price for trade in trades] == expected["trade_price"]
    assert [order.status.name.lower() for order in orders] == expected["order_status"]
    assert [decision.status.value for decision in strategy.order_decisions] == expected["decision_status"]
    assert trades[1].order.properties._gambit_roll_leg == "close"
    assert trades[2].order.properties._gambit_roll_leg == "reopen"
    assert trades[1].order.properties._gambit_roll_id == trades[2].order.properties._gambit_roll_id

    outgoing_final = strategy.account.symbol_pnls[outgoing.symbol].df().row(-1, named=True)
    incoming_final = strategy.account.symbol_pnls[incoming.symbol].df().row(-1, named=True)
    aggregate_final = strategy.df_pnl(group).row(-1, named=True)
    for field in ("position", "realized", "commission", "net_pnl"):
        assert outgoing_final[field] == pytest.approx(expected[f"outgoing_{field}"], abs=tolerance)
    for field in ("position", "unrealized", "commission", "net_pnl"):
        assert incoming_final[field] == pytest.approx(expected[f"incoming_{field}"], abs=tolerance)
    assert aggregate_final["net_pnl"] == pytest.approx(expected["aggregate_net_pnl"], abs=tolerance)
    assert aggregate_final["equity"] == pytest.approx(expected["aggregate_equity"], abs=tolerance)


def test_vwap_acceptance_fill_is_invariant_to_future_market_values() -> None:
    case = corpus()["vwap_causality_case"]
    expected = case["expected"]
    group = ContractGroup.get(f"acceptance-{case['name']}")
    contract = Contract.create(case["name"], group)
    timestamps = np.asarray(case["timestamps"], dtype="datetime64[ns]")
    observed: list[tuple[float, int, str, np.datetime64]] = []

    for sign in (-1, 1):
        for variant in case["future_variants"]:
            prices = np.asarray([*case["known_prices"], *variant["prices"]], dtype=float)
            volumes = np.asarray([*case["known_volumes"], *variant["volumes"]], dtype=float)
            order = VWAPOrder(
                contract=contract,
                timestamp=timestamps[case["submitted_index"]],
                qty=sign * case["order_qty"],
                time_in_force=TimeInForce.GTC,
                vwap_end_time=timestamps[case["end_index"]],
            )
            trades = VWAPMarketSimulator("price", "volume")(
                [order],
                case["evaluated_index"],
                timestamps,
                {group.name: SimpleNamespace(price=prices, volume=volumes)},
                {},
                SimpleNamespace(),
            )
            assert len(trades) == 1
            trade = trades[0]
            observed.append((trade.price, trade.qty, order.status.name.lower(), trade.timestamp))
            assert trade.price == expected["fill_price"]
            assert trade.qty == sign * case["order_qty"]
            assert order.status.name.lower() == expected["terminal_status"]
            assert trade.timestamp == timestamps[expected["fill_index"]]

    assert {price for price, _qty, _status, _timestamp in observed} == {expected["fill_price"]}


@pytest.mark.parametrize(
    "case", corpus()["invalid_numeric_cases"], ids=lambda case: case["name"]
)
def test_non_finite_financial_inputs_fail_at_admission_without_account_mutation(
    case: dict[str, Any],
) -> None:
    value = numeric_token(case["value"])
    timestamp = np.datetime64("2024-07-08T09:30", "ns")
    timestamps = np.asarray([timestamp])
    group = ContractGroup.get(f"acceptance-{case['name']}")
    contract = Contract.create(case["name"], group)

    if case["boundary"] == "order_qty":
        with pytest.raises(ValueError, match=case["error"]):
            MarketOrder(contract=contract, timestamp=timestamp, qty=value)
        return

    mark = value if case["boundary"] == "account_mark" else 100.0
    account = Account([group], timestamps, lambda *_args: mark, SimpleNamespace())
    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    trade = Trade(contract, order, timestamp, 1, 100.0)
    if case["boundary"].startswith("trade_"):
        setattr(trade, case["boundary"].removeprefix("trade_"), value)

    with pytest.raises(ValueError, match=case["error"]):
        account.add_trades([trade])
    assert account.trade_count == 0
    assert account.symbols() == []
    assert account.trades() == []


def test_nan_mark_carries_the_last_finite_unrealized_pnl() -> None:
    case = corpus()["missing_mark_case"]
    group = ContractGroup.get(f"acceptance-{case['name']}")
    contract = Contract.create(case["name"], group, multiplier=case["multiplier"])
    timestamps = np.asarray(case["timestamps"], dtype="datetime64[ns]")
    marks = [numeric_token(value) for value in case["marks"]]
    account = Account(
        [group],
        timestamps,
        lambda _contract, _timestamps, index, _context: marks[index],
        SimpleNamespace(),
        starting_equity=case["starting_equity"],
    )
    order = MarketOrder(contract=contract, timestamp=timestamps[0], qty=1)
    account.add_trades(
        [Trade(contract, order, timestamps[0], 1, case["execution_price"])]
    )
    account.calc(timestamps[-1])

    pnl = account.symbol_pnls[contract.symbol].df()
    assert pnl["unrealized"].to_list() == case["expected_unrealized"]
    assert [account.equity(timestamp) for timestamp in timestamps] == case["expected_equity"]


@pytest.mark.parametrize(
    "case", corpus()["finite_overflow_cases"], ids=lambda case: case["name"]
)
def test_finite_input_arithmetic_overflow_fails_without_non_finite_publication(
    case: dict[str, Any],
) -> None:
    timestamp = np.datetime64("2024-07-09T09:30", "ns")
    timestamps = np.asarray([timestamp])
    group = ContractGroup.get(f"acceptance-{case['name']}")

    if case["operation"] == "quantity":
        contract = Contract.create(case["name"], group)
        bounds = np.iinfo(np.int_)
        for quantity in (int(bounds.min) - 1, int(bounds.max) + 1):
            with pytest.raises(ValueError, match=case["error"]):
                MarketOrder(contract=contract, timestamp=timestamp, qty=quantity)
        return

    if case["operation"] == "aggregate":
        account = Account([group], timestamps, lambda *_args: 100.0, SimpleNamespace())
        trades = []
        for index in range(2):
            contract = Contract.create(f"{case['name']}-{index}", group)
            order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
            trades.append(
                Trade(
                    contract,
                    order,
                    timestamp,
                    1,
                    100.0,
                    fee=case["rebate_per_contract"],
                )
            )
        account.add_trades(trades)
        for _attempt in range(2):
            with pytest.raises(OverflowError, match=case["error"]):
                account.equity(timestamp)
        with pytest.raises(OverflowError, match=case.get("table_error", case["error"])):
            account.df_account_pnl()
        return

    starting_equity = case.get("starting_equity", 1_000.0)
    multiplier = case.get("multiplier", 1.0)
    contract = Contract.create(case["name"], group, multiplier=multiplier)
    initial_mark = (
        case["mark_price"]
        if case["operation"] == "unrealized"
        else case.get("entry_price", 100.0)
    )
    account = Account(
        [group],
        timestamps,
        lambda *_args: initial_mark,
        SimpleNamespace(),
        starting_equity=starting_equity,
    )

    if case["operation"] == "cost":
        orders = [MarketOrder(contract=contract, timestamp=timestamp, qty=1) for _ in range(2)]
        trades = [
            Trade(contract, order, timestamp, 1, 100.0, fee=case["fee_per_trade"])
            for order in orders
        ]
        with pytest.raises(OverflowError, match=case["error"]):
            account.add_trades(trades)
        assert account.trade_count == 0
        assert account.symbols() == []
        return

    order = MarketOrder(contract=contract, timestamp=timestamp, qty=1)
    rebate = case.get("rebate", 0.0)
    opening_trade = Trade(
        contract,
        order,
        timestamp,
        1,
        case.get("entry_price", 100.0),
        fee=rebate,
    )

    if case["operation"] == "unrealized":
        with pytest.raises(OverflowError, match=case["error"]):
            account.add_trades([opening_trade])
        assert account.trade_count == 0
        assert account.symbols() == []
        return

    account.add_trades([opening_trade])
    if case["operation"] == "realized":
        equity_before = account.equity(timestamp)
        closing_order = MarketOrder(contract=contract, timestamp=timestamp, qty=-1)
        closing_trade = Trade(
            contract, closing_order, timestamp, -1, case["exit_price"]
        )
        with pytest.raises(OverflowError, match=case["error"]):
            account.add_trades([closing_trade])
        assert account.trade_count == 1
        assert account.position(group, timestamp) == 1
        assert account.equity(timestamp) == equity_before
        return

    with pytest.raises(OverflowError, match=case["error"]):
        account.equity(timestamp)
    with pytest.raises(OverflowError, match=case["error"]):
        account.df_account_pnl()


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
