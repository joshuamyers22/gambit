"""Translate multi-currency exposure before portfolio-risk aggregation."""

from __future__ import annotations

import numpy as np
import polars as pl

import gambit

VALUATION_TIME = np.datetime64("2026-08-29T16:00")


def main() -> None:
    local_exposures = pl.DataFrame(
        {
            "symbol": ["US-STOCK", "EU-STOCK"],
            "contract_group": ["equities", "equities"],
            "asset_class": ["equity", "equity"],
            "currency": ["USD", "EUR"],
            "quantity": [10.0, 20.0],
            "price": [100.0, 50.0],
            "multiplier": [1.0, 1.0],
            "net_exposure": [1_000.0, 1_000.0],
            "gross_exposure": [1_000.0, 1_000.0],
        }
    )
    context = gambit.CalculationContext(
        valuation_time=VALUATION_TIME,
        market_data_as_of=VALUATION_TIME - np.timedelta64(1, "m"),
        base_currency="USD",
    )
    snapshot = gambit.FxRateSnapshot(
        base_currency="USD",
        as_of=context.market_data_as_of,
        rates={"EUR": 1.20},  # 1 EUR = 1.20 USD
        source="example-closing-fix",
    )
    exposures = gambit.translate_exposures(local_exposures, snapshot, context)
    result = gambit.calculate_risk(
        exposures,
        (gambit.NetExposureMeasure(), gambit.GrossExposureMeasure()),
        context,
    )

    print(exposures.select("symbol", "local_currency", "local_net_exposure", "fx_rate", "net_exposure"))
    print("\nBase-currency risk")
    print(result.aggregate())

    assert exposures["net_exposure"].sum() == 2_200.0
    total = result.filter(measure="net_exposure").aggregate(by=())
    assert total["unit"][0] == "USD"
    assert total["value"][0] == 2_200.0


if __name__ == "__main__":
    main()
