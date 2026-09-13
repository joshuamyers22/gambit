"""Reconcile attributed gross/net P&L with turnover and explicit trade costs."""

import numpy as np
import polars as pl

from gambit.cost_diagnostics import CostPeriod, CostTurnoverAnalyzer

pnl = pl.DataFrame(
    {
        "timestamp": np.array(["2026-01-02", "2026-01-03"], dtype="datetime64[ns]"),
        "symbol": ["FUTURE", "FUTURE"],
        "rule": ["trend", "trend"],
        # Gross P&L is after execution-price effects but before explicit charges.
        "gross_pnl": [250.0, -100.0],
        "net_pnl": [247.0, -103.0],
    }
)
trades = pl.DataFrame(
    {
        "timestamp": np.array(["2026-01-02", "2026-01-03"], dtype="datetime64[ns]"),
        "symbol": ["FUTURE", "FUTURE"],
        "rule": ["trend", "trend"],
        "qty": [2.0, -2.0],
        "price": [5_000.0, 5_010.0],
        "multiplier": [50.0, 50.0],
        "fee": [1.0, 1.0],
        "commission": [2.0, 2.0],
        # Optional immutable fill diagnostics keep price effects separate from charges.
        "reference_price": [4_999.75, 5_010.25],
        "modeled_price_adjustment": [0.20, -0.20],
        "rounding_price_adjustment": [0.05, -0.05],
        "price_effect": [25.0, 25.0],
        "price_effect_model": ["BidAskSpreadSlippage", "BidAskSpreadSlippage"],
    }
)

report = CostTurnoverAnalyzer(
    capital=1_000_000.0,
    period=CostPeriod.MONTHLY,
).analyze(pnl, trades)

assert report.data.select(
    "gross_pnl", "net_pnl", "explicit_cost", "traded_notional", "turnover", "execution_price_effect"
).row(0) == (150.0, 144.0, 6.0, 1_001_000.0, 1.001, 50.0)
print(report.data)
print(report.price_effects)
