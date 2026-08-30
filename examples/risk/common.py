"""Shared deterministic portfolio fixture for the executable risk examples."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

import gambit

VALUATION_TIME = np.datetime64("2026-08-28T16:00")


def build_demo_account() -> tuple[gambit.Account, dict[str, gambit.Contract]]:
    """Return a marked long-equity/short-futures portfolio with no external data."""
    gambit.Contract.clear_cache()
    gambit.ContractGroup.clear_cache()

    equities = gambit.ContractGroup.get("example-equities")
    futures = gambit.ContractGroup.get("example-futures")
    contracts = {
        "ACME": gambit.Contract.create(
            "ACME",
            equities,
            instrument_spec=gambit.InstrumentSpec(asset_class=gambit.AssetClass.EQUITY, currency="USD"),
        ),
        "INDEX-FUT": gambit.Contract.create(
            "INDEX-FUT",
            futures,
            multiplier=50.0,
            instrument_spec=gambit.InstrumentSpec(asset_class=gambit.AssetClass.FUTURE, currency="USD"),
        ),
    }
    prices = {"ACME": 125.0, "INDEX-FUT": 5_000.0}

    def price(contract, _timestamps, _index, context):
        return context.prices[contract.symbol]

    account = gambit.Account(
        [equities, futures],
        np.array([VALUATION_TIME]),
        price,
        SimpleNamespace(prices=prices),
    )
    account.add_trades(
        [
            gambit.Trade(
                contracts["ACME"],
                gambit.MarketOrder(contract=contracts["ACME"], timestamp=VALUATION_TIME, qty=800),
                VALUATION_TIME,
                800,
                120.0,
            ),
            gambit.Trade(
                contracts["INDEX-FUT"],
                gambit.MarketOrder(contract=contracts["INDEX-FUT"], timestamp=VALUATION_TIME, qty=-2),
                VALUATION_TIME,
                -2,
                4_980.0,
            ),
        ]
    )
    return account, contracts
