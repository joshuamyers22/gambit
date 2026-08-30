"""Clip proposed exposure and enforce persisted pre-trade control state."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import polars as pl

import gambit

TIMESTAMP = np.datetime64("2026-08-29T16:00")


def main() -> None:
    proposed = pl.DataFrame(
        {
            "symbol": ["STOCK", "FUTURE", "BOND"],
            "strategy": ["trend", "trend", "carry"],
            "contract_group": ["equity", "rates", "rates"],
            "net_exposure": [600_000.0, -800_000.0, 400_000.0],
        }
    )
    limits = gambit.HierarchicalExposureLimiter(
        [
            gambit.ExposureLimit(gambit.ControlLevel.INSTRUMENT, 500_000.0, "STOCK"),
            gambit.ExposureLimit(gambit.ControlLevel.GROUP, 900_000.0, "rates"),
            gambit.ExposureLimit(gambit.ControlLevel.STRATEGY, 1_000_000.0, "trend"),
            gambit.ExposureLimit(gambit.ControlLevel.PORTFOLIO, 1_200_000.0),
        ]
    ).apply(proposed)

    print("Clipped exposure")
    print(limits.positions)
    print("\nLimit decisions")
    print(limits.diagnostics)

    group = gambit.ContractGroup.get("control-example")
    contract = gambit.Contract.create("CONTROL-EXAMPLE", group)
    strategy = gambit.Strategy(np.array([TIMESTAMP]), [group], lambda *_args: 100.0)
    order = gambit.MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=5.0)
    book = gambit.TradingOverrideBook(
        [
            gambit.TradingOverride(
                gambit.ControlLevel.INSTRUMENT,
                gambit.TradingMode.NO_TRADE,
                TIMESTAMP,
                "manual instrument halt",
                key=contract.symbol,
            )
        ]
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "overrides.json"
        book.save(path)
        restored = gambit.TradingOverrideBook.load(path)

    decision = gambit.decide_order(
        order,
        gambit.RiskContext(strategy.account, TIMESTAMP, []),
        [
            gambit.TradingOverridePolicy(restored, strategy="trend"),
            gambit.RollingTradeBudget(100.0, np.timedelta64(1, "D")),
        ],
    )
    print("\nOrder decision")
    print(decision)

    assert limits.positions["gross_exposure"].sum() <= 1_200_000.0
    assert decision.status is gambit.DecisionStatus.REJECTED
    assert decision.code == "no_trade_override"


if __name__ == "__main__":
    main()
