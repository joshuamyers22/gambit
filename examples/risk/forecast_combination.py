"""Scale, cap, and combine rule forecasts with complete contribution evidence."""

import numpy as np
import polars as pl

from gambit.forecasting import FixedForecastCombiner, ForecastCombinationEstimator, ForecastScaleCap

raw = pl.DataFrame(
    {
        "timestamp": np.array(["2026-01-02"] * 4, dtype="datetime64[ns]"),
        "symbol": ["A", "A", "B", "B"],
        "rule": ["carry", "momentum", "carry", "momentum"],
        "raw_forecast": [-1.0, 2.0, 1.0, 1.0],
    }
)

scaled = ForecastScaleCap(
    {"carry": 1.0, "momentum": 2.0},
    cap=3.0,
).transform(raw)
result = FixedForecastCombiner(
    {"carry": 0.75, "momentum": 0.25},
).combine(scaled)

assert result.forecasts.select("symbol", "raw_forecast").rows() == [
    ("A", 0.0),
    ("B", 1.25),
]
assert result.contributions.select("symbol", "rule", "available", "scalar", "weight", "contribution").rows() == [
    ("A", "carry", True, 1.0, 0.75, -0.75),
    ("A", "momentum", True, 2.0, 0.25, 0.75),
    ("B", "carry", True, 1.0, 0.75, 0.75),
    ("B", "momentum", True, 2.0, 0.25, 0.5),
]

# Fit weights, correlation, and a bounded diversification multiplier only from
# an explicitly historical wide frame. Orthogonal, equal-volatility rules get
# equal weights and the configured multiplier cap of 1.25.
history = pl.DataFrame(
    {
        "timestamp": np.arange(np.datetime64("2025-12-28"), np.datetime64("2026-01-01"), dtype="datetime64[D]").astype(
            "datetime64[ns]"
        ),
        "carry": [1.0, -1.0, 1.0, -1.0],
        "momentum": [1.0, 1.0, -1.0, -1.0],
    }
)
fitted = ForecastCombinationEstimator(
    min_observations=4,
    max_diversification_multiplier=1.25,
).fit(history, timestamp_column="timestamp", rule_columns=["carry", "momentum"])
estimated = fitted.combiner().combine(scaled)

assert fitted.weights == {"carry": 0.5, "momentum": 0.5}
assert fitted.diversification_multiplier == 1.25
assert estimated.forecasts[0, "raw_forecast"] == 1.25
