from types import SimpleNamespace

import numpy as np
import pytest

from gambit.execution_costs import BidAskSpreadSlippage, NotionalCharge, PerOrderCharge, SquareRootMarketImpact
from gambit.pq_types import Contract, MarketOrder
from gambit.risk import DecisionStatus, MaxVolumeParticipation, RiskContext, decide_order
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator

TIMESTAMP = np.datetime64("2024-01-02T10:00")
TIMESTAMPS = np.array([TIMESTAMP])


def _price(_contract, _timestamps, _index, _context):
    return 100.0


@pytest.mark.parametrize(("qty", "expected"), [(2.0, 100.1), (-2.0, 99.9)])
def test_spread_slippage_crosses_half_spread(qty, expected) -> None:
    contract = Contract.create(f"SPREAD-{qty}")
    order = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=qty)
    simulator = SimpleMarketSimulator(_price, slippage_model=BidAskSpreadSlippage(0.2))

    trade = simulator([order], 0, TIMESTAMPS, {}, {}, SimpleNamespace())[0]

    assert trade.price == expected


def test_execution_supports_separate_commission_and_fee_models() -> None:
    contract = Contract.create("CHARGES", multiplier=10)
    order = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=2)
    simulator = SimpleMarketSimulator(
        _price,
        commission_model=PerOrderCharge(1.25),
        fee_model=NotionalCharge(0.001),
    )

    trade = simulator([order], 0, TIMESTAMPS, {}, {}, SimpleNamespace())[0]

    assert trade.commission == pytest.approx(1.25)
    assert trade.fee == pytest.approx(2.0)


def test_square_root_market_impact_scales_with_participation() -> None:
    contract = Contract.create("IMPACT")
    order = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=100)
    model = SquareRootMarketImpact(available_volume=10_000, volatility=0.02, coefficient=0.5)

    assert model.adjustment(order, 100.0) == pytest.approx(0.1)


def test_volume_policy_rejects_excess_participation() -> None:
    contract = Contract.create("LIQUIDITY")
    order = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=101)
    strategy = Strategy(TIMESTAMPS, [contract.contract_group], _price)
    policy = MaxVolumeParticipation(0.1, lambda _order, _timestamp: 1_000)

    decision = decide_order(order, RiskContext(strategy.account, TIMESTAMP, []), [policy])

    assert decision.status is DecisionStatus.REJECTED
    assert decision.code == "volume_participation_exceeded"
