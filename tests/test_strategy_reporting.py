from typing import Any

import numpy as np
import polars as pl
import pytest

from gambit.pq_types import ContractGroup
from gambit.strategy import Strategy


class FakeReturnReporter:
    def __init__(self) -> None:
        self.metric_calls: list[tuple[np.ndarray, np.ndarray, float, int]] = []
        self.display_calls: list[tuple[dict[str, Any], int]] = []
        self.plot_calls: list[dict[str, Any]] = []

    def metrics(
        self,
        timestamps: np.ndarray,
        returns: np.ndarray,
        starting_equity: float,
        *,
        periods_per_year: int = 0,
    ) -> dict[str, Any]:
        self.metric_calls.append((timestamps, returns, starting_equity, periods_per_year))
        return {"sharpe": 1.25}

    def display(self, metrics: dict[str, Any], *, float_precision: int = 4) -> None:
        self.display_calls.append((metrics, float_precision))

    def plot(self, metrics: dict[str, Any]) -> str:
        self.plot_calls.append(metrics)
        return "figure"


def _strategy(reporter: FakeReturnReporter) -> Strategy:
    timestamp = np.datetime64("2026-01-02")
    strategy = Strategy(
        np.array([timestamp]),
        [ContractGroup.get("return-reporter")],
        lambda *_args: 100.0,
        return_reporter=reporter,
    )
    returns = pl.DataFrame(
        {
            "timestamp": pl.Series("timestamp", np.array([timestamp], dtype="datetime64[ns]")),
            "ret": [0.01],
        }
    )
    strategy.__dict__["df_returns"] = lambda _group=None: returns
    return strategy


def test_evaluate_returns_delegates_analytics_and_presentation_to_injected_port() -> None:
    reporter = FakeReturnReporter()

    result = _strategy(reporter).evaluate_returns(periods_per_year=252, float_precision=6)

    assert result == {"sharpe": 1.25}
    assert reporter.metric_calls[0][2:] == (1_000_000.0, 252)
    assert reporter.display_calls == [({"sharpe": 1.25}, 6)]
    assert reporter.plot_calls == [{"sharpe": 1.25}]


def test_plot_returns_returns_value_from_injected_port() -> None:
    reporter = FakeReturnReporter()

    result = _strategy(reporter).plot_returns()

    assert result == "figure"
    assert reporter.display_calls == []


def test_empty_return_series_fails_before_reporting_adapter_is_called() -> None:
    reporter = FakeReturnReporter()
    strategy = _strategy(reporter)
    strategy.__dict__["df_returns"] = lambda _group=None: pl.DataFrame(
        schema={"timestamp": pl.Datetime("ns"), "ret": pl.Float64}
    )

    with pytest.raises(ValueError, match="at least one return observation"):
        strategy.evaluate_returns(plot=False, display_summary=False)

    assert reporter.metric_calls == []
