"""Typed dispatch between native integral and reference fractional P&L kernels."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from gambit.compute_pnl import calc_trade_pnl, calc_trade_pnl_old


def _can_use_integral_kernel(*quantity_arrays: np.ndarray) -> bool:
    integer_limits = np.iinfo(np.int_)
    return all(
        np.all(np.isfinite(values))
        and np.all(values == np.trunc(values))
        and np.all(values >= integer_limits.min)
        and np.all(values <= integer_limits.max)
        for values in quantity_arrays
    )


def calculate_trade_pnl(
    open_quantities: NDArray[np.float64],
    open_prices: NDArray[np.float64],
    new_quantities: NDArray[np.float64],
    new_prices: NDArray[np.float64],
    multiplier: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64], float]:
    """Net FIFO executions while preserving fractional quantities."""
    if _can_use_integral_kernel(open_quantities, new_quantities):
        quantities, prices, realized = calc_trade_pnl(
            open_quantities.astype(np.int_),
            open_prices,
            new_quantities.astype(np.int_),
            new_prices,
            multiplier,
        )
    else:
        quantities, prices, realized = calc_trade_pnl_old(
            open_quantities,
            open_prices,
            new_quantities,
            new_prices,
            multiplier,
        )
    return quantities.astype(float), prices.astype(float), float(realized)


__all__ = ["calculate_trade_pnl"]
