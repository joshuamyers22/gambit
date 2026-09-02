"""Evaluator-backed adapter for strategy return reporting."""

from __future__ import annotations

from typing import Any

import numpy as np

from gambit.evaluator import compute_return_metrics, display_return_metrics, plot_return_metrics


class EvaluatorReturnReporter:
    def metrics(
        self,
        timestamps: np.ndarray,
        returns: np.ndarray,
        starting_equity: float,
        *,
        periods_per_year: int = 0,
    ) -> dict[str, Any]:
        evaluation = compute_return_metrics(
            timestamps,
            returns,
            starting_equity,
            periods_per_year=periods_per_year,
        )
        return evaluation.metrics()

    def display(self, metrics: dict[str, Any], *, float_precision: int = 4) -> None:
        display_return_metrics(metrics, float_precision=float_precision)

    def plot(self, metrics: dict[str, Any]) -> Any:
        return plot_return_metrics(metrics)


__all__ = ["EvaluatorReturnReporter"]
