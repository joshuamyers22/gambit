from types import MappingProxyType

import numpy as np
import polars as pl
import pytest

from gambit.calculation import CalculationContext
from gambit.currency import FxRateSnapshot, translate_exposures

TIMESTAMP = np.datetime64("2026-08-29T16:00")


def _exposures() -> pl.DataFrame:
    return pl.DataFrame(
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


def test_fx_snapshot_is_normalized_and_immutable() -> None:
    source = {"eur": 1.2}
    snapshot = FxRateSnapshot("usd", TIMESTAMP, source, source="closing-fix")
    source["eur"] = 2.0

    assert snapshot.base_currency == "USD"
    assert snapshot.rate("USD") == 1.0
    assert snapshot.rate("eur") == 1.2
    assert isinstance(snapshot.rates, MappingProxyType)
    with pytest.raises(TypeError):
        snapshot.rates["EUR"] = 2.0  # type: ignore[index]


def test_translate_exposures_retains_local_audit_columns() -> None:
    snapshot = FxRateSnapshot("USD", TIMESTAMP - np.timedelta64(1, "m"), {"EUR": 1.2}, source="closing-fix")
    context = CalculationContext(TIMESTAMP, market_data_as_of=TIMESTAMP, base_currency="USD")

    translated = translate_exposures(_exposures(), snapshot, context)

    assert translated["currency"].to_list() == ["USD", "USD"]
    assert translated["local_currency"].to_list() == ["USD", "EUR"]
    assert translated["fx_rate"].to_list() == [1.0, 1.2]
    assert translated["price"].to_list() == [100.0, 60.0]
    assert translated["net_exposure"].to_list() == [1_000.0, 1_200.0]
    assert translated["local_net_exposure"].to_list() == [1_000.0, 1_000.0]
    assert translated["fx_source"].to_list() == ["closing-fix", "closing-fix"]
    assert translated["fx_as_of"].to_numpy()[0] == snapshot.as_of


def test_translate_exposures_rejects_future_missing_and_wrong_base_rates() -> None:
    context = CalculationContext(TIMESTAMP, market_data_as_of=TIMESTAMP, base_currency="USD")
    with pytest.raises(ValueError, match="after the calculation cutoff"):
        translate_exposures(_exposures(), FxRateSnapshot("USD", TIMESTAMP + np.timedelta64(1, "m"), {"EUR": 1.2}), context)
    with pytest.raises(ValueError, match="missing for"):
        translate_exposures(_exposures(), FxRateSnapshot("USD", TIMESTAMP, {}), context)
    with pytest.raises(ValueError, match="base currencies must match"):
        translate_exposures(_exposures(), FxRateSnapshot("GBP", TIMESTAMP, {"USD": 0.8, "EUR": 0.9}), context)
    with pytest.raises(ValueError, match="must equal one"):
        FxRateSnapshot("USD", TIMESTAMP, {"USD": 0.9})
    with pytest.raises(ValueError, match="duplicate FX currency"):
        FxRateSnapshot("USD", TIMESTAMP, {"eur": 1.2, "EUR": 1.3})
