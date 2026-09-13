"""Convert base-currency exposure targets to whole-contract incremental orders."""

import numpy as np
import polars as pl

import gambit
from common import VALUATION_TIME, build_demo_account

account, contracts = build_demo_account()
contract_list = [contracts["ACME"], contracts["INDEX-FUT"]]
exposures = pl.DataFrame(
    {
        "symbol": ["ACME", "INDEX-FUT"],
        "currency": ["USD", "USD"],
        "net_exposure": [125_000.0, -750_000.0],
    }
)
prices = pl.DataFrame(
    {
        "symbol": ["ACME", "INDEX-FUT"],
        "currency": ["USD", "USD"],
        "price": [125.0, 5_000.0],
        "as_of": np.array([VALUATION_TIME, VALUATION_TIME], dtype="datetime64[ns]"),
    }
)
pending = gambit.MarketOrder(contract=contracts["ACME"], timestamp=VALUATION_TIME, qty=100)

result = gambit.ExecutableTargetBuilder(
    {
        "ACME": gambit.TradableUnitRule(),
        "INDEX-FUT": gambit.TradableUnitRule(),
    }
).build(
    exposures,
    contract_list,
    prices,
    gambit.FxRateSnapshot("USD", VALUATION_TIME, {}),
    gambit.CalculationContext(VALUATION_TIME, base_currency="USD"),
    account,
    pending_orders=[pending],
)

assert result.positions["target_quantity"].to_list() == [1_000, -3]
assert [(order.contract.symbol, order.qty) for order in result.orders] == [("ACME", 100), ("INDEX-FUT", -1)]
print(result.positions)
