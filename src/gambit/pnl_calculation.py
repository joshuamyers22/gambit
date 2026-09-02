"""Typed boundary for the native whole-unit P&L kernel."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from gambit.compute_pnl import calc_trade_pnl


def calculate_trade_pnl(
    open_quantities: NDArray[np.int_],
    open_prices: NDArray[np.float64],
    new_quantities: NDArray[np.int_],
    new_prices: NDArray[np.float64],
    multiplier: float,
) -> tuple[NDArray[np.int_], NDArray[np.float64], float]:
    """Net FIFO executions through the native whole-unit kernel."""
    quantities, prices, realized = calc_trade_pnl(
        open_quantities,
        open_prices,
        new_quantities,
        new_prices,
        multiplier,
    )
    return quantities, prices, float(realized)


__all__ = ["calculate_trade_pnl"]
