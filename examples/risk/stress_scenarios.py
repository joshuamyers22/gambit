"""Run named, relative, absolute, and pattern-based stress scenarios."""

from __future__ import annotations

import numpy as np
from common import VALUATION_TIME, build_demo_account

import gambit


def main() -> None:
    account, _contracts = build_demo_account()
    scenarios = (
        gambit.StressScenario("market-down-5", {"*": -0.05}),
        gambit.StressScenario("equity-crash", {"equity": -0.20}),
        gambit.StressScenario(
            "basis-dislocation",
            market_shocks=(
                gambit.MarketDataShock(
                    gambit.MarketDataPattern(asset_class="equity"),
                    -0.10,
                    gambit.ShockType.RELATIVE,
                ),
                gambit.MarketDataShock(
                    gambit.MarketDataPattern(symbol="INDEX-FUT"),
                    75.0,
                    gambit.ShockType.ABSOLUTE,
                ),
            ),
        ),
    )
    context = gambit.CalculationContext(
        valuation_time=VALUATION_TIME,
        market_data_as_of=VALUATION_TIME - np.timedelta64(1, "m"),
        scenarios=scenarios,
        base_currency="USD",
        provenance_reference="risk-example-2026-08-28",
    )
    report = gambit.analyze_account_risk(account, context)

    print(report.scenario_results)
    print("\nPortfolio summary")
    print(report.summary())

    results = dict(
        report.scenario_results.select("scenario", "stressed_pnl").iter_rows()
    )
    assert results["market-down-5"] == 20_000.0
    assert results["equity-crash"] == -20_000.0
    assert results["basis-dislocation"] == -17_500.0


if __name__ == "__main__":
    main()
