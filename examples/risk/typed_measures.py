"""Calculate composable risk measures and aggregate their long-form output."""

from __future__ import annotations

from common import VALUATION_TIME, build_demo_account

import gambit


def main() -> None:
    account, _contracts = build_demo_account()
    exposures = gambit.account_exposures(account, VALUATION_TIME)
    downside = gambit.StressScenario("down-10", {"*": -0.10})
    result = gambit.calculate_risk(
        exposures,
        measures=(
            gambit.PriceMeasure(),
            gambit.NetExposureMeasure(),
            gambit.GrossExposureMeasure(),
            gambit.ScenarioPnlMeasure(downside),
        ),
        timestamp=gambit.CalculationContext(VALUATION_TIME, base_currency="USD"),
    )

    print("Long-form risk data")
    print(result.data)
    print("\nAggregated by measure and scenario")
    print(result.aggregate())
    print("\nScenario contribution by asset class")
    print(result.filter(measure="scenario_pnl").aggregate(by=("scenario", "asset_class")))

    total_stress = result.filter(measure="scenario_pnl").aggregate(by=())["value"][0]
    assert total_stress == 40_000.0


if __name__ == "__main__":
    main()
