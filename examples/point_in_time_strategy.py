"""Run a built-in indicator against revision-aware point-in-time prices."""

from __future__ import annotations

import numpy as np
import polars as pl

import gambit


def main() -> None:
    timestamps = np.array(
        ["2024-01-02T09:30", "2024-01-02T09:31", "2024-01-02T09:32"],
        dtype="datetime64[ns]",
    )
    rows = pl.DataFrame(
        {
            "symbol": ["EXAMPLE", "EXAMPLE"],
            "field": ["price", "price"],
            "observation_time": np.array(
                ["2024-01-02T09:30", "2024-01-02T09:31"], dtype="datetime64[ns]"
            ),
            "available_time": np.array(
                ["2024-01-02T09:30", "2024-01-02T09:32"], dtype="datetime64[ns]"
            ),
            "revision": ["initial", "initial"],
            "value": [100.0, 102.0],
        }
    )
    data = gambit.PointInTimeMarketData(
        rows,
        source="example-bars",
        dataset_revision="example-snapshot-1",
    )
    price_function = gambit.PointInTimePriceFunction(
        data,
        allow_previous=True,
        max_age=np.timedelta64(2, "m"),
    )
    indicator = gambit.PointInTimeIndicator(
        data,
        symbol="EXAMPLE",
        allow_previous=True,
        max_age=np.timedelta64(2, "m"),
    )
    group = gambit.ContractGroup.get("point-in-time-example")
    strategy = gambit.Strategy(timestamps, [group], price_function)
    strategy.add_indicator("published_price", indicator)
    result = strategy.run()

    values = strategy.indicator_values[group.name].published_price.tolist()
    assert values == [100.0, 100.0, 102.0]
    assert result.provenance.input_fingerprints == {"market_data": data.fingerprint}
    assert not hasattr(data, "frame")
    print(f"Causal indicator values: {values}")
    print(f"Dataset fingerprint: {data.fingerprint}")


if __name__ == "__main__":
    main()
