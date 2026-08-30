import json

import numpy as np
import polars as pl
import pytest

from gambit.control_plane import (
    ControlLevel,
    ExposureLimit,
    HierarchicalExposureLimiter,
    RollingTradeBudget,
    TradingMode,
    TradingOverride,
    TradingOverrideBook,
    TradingOverridePolicy,
)
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Trade
from gambit.risk import DecisionStatus, RiskContext, decide_order
from gambit.strategy import Strategy

TIMESTAMP = np.datetime64("2026-08-29T16:00")


def _positions() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["A", "B", "C"],
            "strategy": ["trend", "trend", "carry"],
            "contract_group": ["equity", "rates", "rates"],
            "net_exposure": [600.0, -800.0, 400.0],
        }
    )


def _order_fixture(
    symbol: str = "A",
    group_name: str = "equity",
    timestamps: np.ndarray | None = None,
) -> tuple[Strategy, MarketOrder]:
    group = ContractGroup.get(group_name)
    contract = Contract.create(symbol, group)
    strategy = Strategy(
        np.array([TIMESTAMP]) if timestamps is None else timestamps,
        [group],
        lambda *_args: 100.0,
    )
    return strategy, MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=5.0)


def test_hierarchical_limits_clip_children_and_reconcile_multipliers() -> None:
    limiter = HierarchicalExposureLimiter(
        [
            ExposureLimit(ControlLevel.INSTRUMENT, 500.0, "A"),
            ExposureLimit(ControlLevel.GROUP, 900.0, "rates"),
            ExposureLimit(ControlLevel.STRATEGY, 1_000.0, "trend"),
            ExposureLimit(ControlLevel.PORTFOLIO, 1_200.0),
        ]
    )

    result = limiter.apply(_positions())

    assert result.positions["pre_limit_net_exposure"].to_list() == [600.0, -800.0, 400.0]
    assert result.positions["gross_exposure"].sum() <= 1_200.0
    assert result.positions.filter(pl.col("strategy") == "trend")["gross_exposure"].sum() <= 1_000.0
    assert result.positions.filter(pl.col("contract_group") == "rates")["gross_exposure"].sum() <= 900.0
    np.testing.assert_allclose(
        result.positions["net_exposure"].to_numpy(),
        result.positions["pre_limit_net_exposure"].to_numpy()
        * result.positions["limit_multiplier"].to_numpy(),
    )
    assert result.diagnostics["level"].to_list() == ["instrument", "group", "strategy", "portfolio"]


def test_hierarchical_limits_reject_ambiguous_or_unmatched_configuration() -> None:
    with pytest.raises(ValueError, match="unique"):
        HierarchicalExposureLimiter(
            [ExposureLimit(ControlLevel.PORTFOLIO, 100.0), ExposureLimit(ControlLevel.PORTFOLIO, 200.0)]
        )
    with pytest.raises(ValueError, match="matches no positions"):
        HierarchicalExposureLimiter([ExposureLimit(ControlLevel.INSTRUMENT, 100.0, "MISSING")]).apply(
            _positions()
        )
    with pytest.raises(ValueError, match="strategy cannot be null"):
        HierarchicalExposureLimiter([ExposureLimit(ControlLevel.PORTFOLIO, 100.0)]).apply(
            _positions().with_columns(pl.Series("strategy", [None, "trend", "carry"]))
        )


def test_override_book_round_trips_and_no_trade_wins(tmp_path) -> None:
    strategy, order = _order_fixture()
    book = TradingOverrideBook(
        [
            TradingOverride(ControlLevel.PORTFOLIO, TradingMode.REDUCE_ONLY, TIMESTAMP, "portfolio caution"),
            TradingOverride(ControlLevel.INSTRUMENT, TradingMode.NO_TRADE, TIMESTAMP, "instrument halt", key="A"),
        ]
    )
    path = tmp_path / "controls" / "overrides.json"

    book.save(path)
    restored = TradingOverrideBook.load(path)
    decision = decide_order(order, RiskContext(strategy.account, TIMESTAMP, []), [TradingOverridePolicy(restored)])

    assert json.loads(path.read_text())["schema_version"] == 1
    assert restored.overrides == book.overrides
    assert decision.status is DecisionStatus.REJECTED
    assert decision.code == "no_trade_override"
    assert decision.message == "instrument halt"


def test_reduce_only_override_allows_only_position_reduction() -> None:
    strategy, order = _order_fixture("REDUCE", "reduce")
    pending = MarketOrder(contract=order.contract, timestamp=TIMESTAMP, qty=10.0)
    book = TradingOverrideBook(
        [TradingOverride(ControlLevel.PORTFOLIO, TradingMode.REDUCE_ONLY, TIMESTAMP, "de-risk")]
    )
    policy = TradingOverridePolicy(book)

    increase = decide_order(order, RiskContext(strategy.account, TIMESTAMP, [pending]), [policy])
    reduction = MarketOrder(contract=order.contract, timestamp=TIMESTAMP, qty=-4.0)
    reduce = decide_order(reduction, RiskContext(strategy.account, TIMESTAMP, [pending]), [policy])

    assert increase.code == "reduce_only_override"
    assert reduce.status is DecisionStatus.ACCEPTED


def test_expired_override_does_not_apply() -> None:
    strategy, order = _order_fixture("EXPIRED", "expired")
    book = TradingOverrideBook(
        [
            TradingOverride(
                ControlLevel.PORTFOLIO,
                TradingMode.NO_TRADE,
                TIMESTAMP - np.timedelta64(2, "h"),
                "old halt",
                expires_at=TIMESTAMP - np.timedelta64(1, "h"),
            )
        ]
    )

    decision = decide_order(order, RiskContext(strategy.account, TIMESTAMP, []), [TradingOverridePolicy(book)])

    assert decision.status is DecisionStatus.ACCEPTED


def test_override_book_rejects_invalid_persisted_schema(tmp_path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("[]")

    with pytest.raises(ValueError, match="root must be an object"):
        TradingOverrideBook.load(path)


def test_rolling_budget_counts_executed_pending_and_proposed_quantity() -> None:
    historical_timestamp = TIMESTAMP - np.timedelta64(30, "m")
    strategy, order = _order_fixture("BUDGET", "budget", np.array([historical_timestamp, TIMESTAMP]))
    historical_order = MarketOrder(
        contract=order.contract,
        timestamp=historical_timestamp,
        qty=4.0,
    )
    strategy.account.add_trades(
        [Trade(order.contract, historical_order, historical_order.timestamp, 4.0, 100.0)]
    )
    pending = MarketOrder(contract=order.contract, timestamp=TIMESTAMP, qty=-3.0)
    policy = RollingTradeBudget(10.0, np.timedelta64(1, "h"), ControlLevel.INSTRUMENT, "BUDGET")

    decision = decide_order(order, RiskContext(strategy.account, TIMESTAMP, [pending]), [policy])

    assert decision.status is DecisionStatus.REJECTED
    assert decision.code == "rolling_trade_budget_exceeded"
    assert "12" in decision.message


def test_rolling_budget_excludes_trades_outside_window_and_scope() -> None:
    old_timestamp = TIMESTAMP - np.timedelta64(2, "h")
    strategy, order = _order_fixture("WINDOW", "window", np.array([old_timestamp, TIMESTAMP]))
    old_order = MarketOrder(
        contract=order.contract,
        timestamp=old_timestamp,
        qty=100.0,
    )
    strategy.account.add_trades([Trade(order.contract, old_order, old_order.timestamp, 100.0, 100.0)])
    policy = RollingTradeBudget(6.0, np.timedelta64(1, "h"), ControlLevel.INSTRUMENT, "WINDOW")

    decision = decide_order(order, RiskContext(strategy.account, TIMESTAMP, []), [policy])

    assert decision.status is DecisionStatus.ACCEPTED


def test_rolling_budget_rejects_calendar_duration() -> None:
    with pytest.raises(ValueError, match="fixed-duration"):
        RollingTradeBudget(10.0, np.timedelta64(1, "M"))
