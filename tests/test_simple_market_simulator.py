from types import SimpleNamespace

import numpy as np
import pytest

from gambit.pq_types import Contract, LimitOrder, MarketOrder, OrderStatus
from gambit.strategy_components import SimpleMarketSimulator

TIMESTAMP = np.datetime64("2024-01-02T10:00")
TIMESTAMPS = np.array([TIMESTAMP])


def _price(_contract, _timestamps, _index, _context):
    return 100.0


def _run(order, *, commission=0.0):
    simulator = SimpleMarketSimulator(_price, commission=commission)
    return simulator([order], 0, TIMESTAMPS, {}, {}, SimpleNamespace())


@pytest.mark.parametrize(
    ("qty", "limit_price"),
    [(1.0, 100.0), (1.0, 101.0), (-1.0, 100.0), (-1.0, 99.0)],
)
def test_marketable_limit_orders_fill(qty, limit_price):
    contract = Contract.create(f"MARKETABLE-{qty}-{limit_price}")
    order = LimitOrder(contract=contract, timestamp=TIMESTAMP, qty=qty, limit_price=limit_price)

    trades = _run(order)

    assert len(trades) == 1
    assert trades[0].price == 100.0
    assert order.status is OrderStatus.FILLED


@pytest.mark.parametrize(("qty", "limit_price"), [(1.0, 99.0), (-1.0, 101.0)])
def test_non_marketable_limit_orders_remain_open(qty, limit_price):
    contract = Contract.create(f"RESTING-{qty}-{limit_price}")
    order = LimitOrder(contract=contract, timestamp=TIMESTAMP, qty=qty, limit_price=limit_price)

    trades = _run(order)

    assert trades == []
    assert order.status is OrderStatus.OPEN


@pytest.mark.parametrize("qty", [3.0, -3.0])
def test_commission_is_charged_per_absolute_unit(qty):
    contract = Contract.create(f"COMMISSION-{qty}")
    order = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=qty)

    trades = _run(order, commission=0.25)

    assert trades[0].commission == pytest.approx(0.75)
