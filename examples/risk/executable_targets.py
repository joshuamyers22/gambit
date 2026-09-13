"""Convert base-currency exposure targets to whole-contract incremental orders."""

import numpy as np
import polars as pl
from common import VALUATION_TIME, build_demo_account

import gambit

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
        "ACME": gambit.TradableUnitRule(no_trade_band=15_000.0),
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
    risk_measures=[gambit.NetExposureMeasure()],
    risk_policies=[gambit.MaxPositionQuantity(1_000)],
)

assert result.positions["target_quantity"].to_list() == [1_000, -3]
assert result.positions["buffer_applied"].to_list() == [True, False]
assert [(order.contract.symbol, order.qty) for order in result.orders] == [("INDEX-FUT", -1)]
assert [decision.status for decision in result.decisions] == [gambit.DecisionStatus.ACCEPTED]
assert result.risk is not None
assert result.risk.aggregate()[0, "value"] == -637_500.0
print(result.positions)
print(result.risk.data)
