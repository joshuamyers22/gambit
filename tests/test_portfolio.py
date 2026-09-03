import numpy as np
import pytest

from gambit.portfolio import Portfolio
from gambit.pq_types import ContractGroup
from gambit.strategy import Strategy


def _price(*_args) -> float:
    return 100.0


def test_portfolio_generates_iterations_from_registered_strategies_by_default() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    strategy = Strategy(timestamps, [ContractGroup.get("portfolio-default")], _price)
    portfolio = Portfolio()
    portfolio.add_strategy("alpha", strategy)

    combined, iterations = portfolio._generate_order_iterations(None)

    assert np.array_equal(combined, timestamps)
    assert len(iterations) == 1
    assert iterations[0][0] is strategy
    assert np.array_equal(iterations[0][1], np.array([0, 1]))


def test_portfolio_rejects_duplicate_strategy_registration_atomically() -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")
    original = Strategy(timestamps, [ContractGroup.get("portfolio-original")], _price)
    replacement = Strategy(timestamps, [ContractGroup.get("portfolio-replacement")], _price)
    portfolio = Portfolio()
    portfolio.add_strategy("alpha", original)

    with pytest.raises(ValueError, match="already registered"):
        portfolio.add_strategy("alpha", replacement)

    assert portfolio.strategies["alpha"] is original
    assert replacement.name == "main"


def test_portfolio_rejects_same_strategy_under_second_name() -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")
    strategy = Strategy(timestamps, [ContractGroup.get("portfolio-duplicate-instance")], _price)
    portfolio = Portfolio()
    portfolio.add_strategy("alpha", strategy)

    with pytest.raises(ValueError, match="instance is already registered"):
        portfolio.add_strategy("beta", strategy)

    assert portfolio.strategies == {"alpha": strategy}
    assert strategy.name == "main"


def test_portfolio_run_honors_selected_strategy_in_every_phase() -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")
    alpha = Strategy(timestamps, [ContractGroup.get("portfolio-selected-alpha")], _price)
    beta = Strategy(timestamps, [ContractGroup.get("portfolio-selected-beta")], _price)
    calls = {"alpha": 0, "beta": 0}

    def alpha_indicator(*_args):
        calls["alpha"] += 1
        return np.array([1.0])

    def beta_indicator(*_args):
        calls["beta"] += 1
        return np.array([1.0])

    alpha.add_indicator("value", alpha_indicator)
    beta.add_indicator("value", beta_indicator)
    portfolio = Portfolio()
    portfolio.add_strategy("alpha", alpha)
    portfolio.add_strategy("beta", beta)

    portfolio.run(["alpha"])

    assert calls == {"alpha": 1, "beta": 0}


def test_portfolio_rejects_reversed_rule_range_before_execution() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02"], dtype="datetime64[D]")
    strategy = Strategy(timestamps, [ContractGroup.get("portfolio-reversed-range")], _price)
    portfolio = Portfolio()
    portfolio.add_strategy("alpha", strategy)

    with pytest.raises(ValueError, match="start date cannot be after end date"):
        portfolio.run_rules(["alpha"], timestamps[1], timestamps[0])


def test_portfolio_validates_all_stage_graphs_before_callbacks() -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")
    strategy = Strategy(timestamps, [ContractGroup.get("portfolio-invalid-stage-graph")], _price)
    callback_calls = 0

    def child_indicator(*_args):
        nonlocal callback_calls
        callback_calls += 1
        return np.array([1.0])

    strategy.add_indicator("child", child_indicator, depends_on=["missing"])
    portfolio = Portfolio()
    portfolio.add_strategy("alpha", strategy)

    with pytest.raises(ValueError, match="indicator:missing"):
        portfolio.run(["alpha"])

    assert callback_calls == 0


def test_portfolio_aliases_do_not_mutate_strategy_name() -> None:
    timestamps = np.array(["2026-01-01"], dtype="datetime64[D]")
    strategy = Strategy(timestamps, [ContractGroup.get("portfolio-registry-identity")], _price)
    callback_calls = 0

    def indicator(*_args):
        nonlocal callback_calls
        callback_calls += 1
        return np.array([1.0])

    strategy.add_indicator("value", indicator)
    first_portfolio = Portfolio()
    second_portfolio = Portfolio()
    first_portfolio.add_strategy("alpha", strategy)
    second_portfolio.add_strategy("beta", strategy)

    first_portfolio.run(["alpha"])

    assert strategy.name == "main"
    assert callback_calls == 1
