import numpy as np

from gambit.evaluator import compute_gmean, compute_periods_per_year, compute_sharpe, compute_sortino


def test_public_scalar_metrics_return_python_floats() -> None:
    timestamps = np.array(["2026-01-01", "2026-01-02", "2026-01-03"], dtype="datetime64[D]")
    returns = np.array([0.001, -0.001, 0.002])

    assert type(compute_periods_per_year(timestamps)) is float
    assert type(compute_gmean(timestamps, returns, 252.0)) is float
    assert type(compute_sharpe(returns, 0.001, 252.0)) is float
    assert type(compute_sortino(returns, 0.001, 252.0)) is float
