"""Build contract-level exposure and attribution tables from an account."""

from __future__ import annotations

from common import VALUATION_TIME, build_demo_account

import gambit


def main() -> None:
    account, _contracts = build_demo_account()
    exposures = gambit.account_exposures(account, VALUATION_TIME)
    attribution = gambit.attribute_exposure(exposures, by=("asset_class",))

    print("Contract exposure")
    print(exposures.select("symbol", "quantity", "price", "net_exposure", "gross_exposure"))
    print("\nAttribution by asset class")
    print(attribution)

    assert exposures["net_exposure"].sum() == -400_000.0
    assert exposures["gross_exposure"].sum() == 600_000.0
    assert attribution["gross_share"].sum() == 1.0


if __name__ == "__main__":
    main()
