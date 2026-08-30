"""Evaluate auditable pre-trade limits before an order reaches execution."""

from __future__ import annotations

from common import VALUATION_TIME, build_demo_account

import gambit


def main() -> None:
    account, contracts = build_demo_account()
    proposed = gambit.MarketOrder(contract=contracts["ACME"], timestamp=VALUATION_TIME, qty=250)
    pending = gambit.MarketOrder(contract=contracts["ACME"], timestamp=VALUATION_TIME, qty=100)
    policies = (
        gambit.MaxOrderQuantity(500),
        gambit.MaxPositionQuantity(1_000),
        gambit.MaxVolumeParticipation(
            maximum_fraction=0.10,
            volume=lambda order, timestamp: 10_000.0,
        ),
        gambit.InstrumentTradabilityPolicy(),
    )
    context = gambit.RiskContext(account, VALUATION_TIME, open_orders=(pending,))
    decision = gambit.decide_order(proposed, context, policies)

    print(
        {
            "status": decision.status.value,
            "policy": decision.policy,
            "code": decision.code,
            "message": decision.message,
            "proposed_qty": decision.proposed_qty,
        }
    )

    assert context.projected_position(proposed) == 1_150.0
    assert decision.status is gambit.DecisionStatus.REJECTED
    assert decision.code == "position_quantity_exceeded"


if __name__ == "__main__":
    main()
