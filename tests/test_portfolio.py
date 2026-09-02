import numpy as np

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
