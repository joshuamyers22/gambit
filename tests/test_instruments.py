import numpy as np
import pytest

from gambit.instruments import AssetClass, InstrumentSpec, Tradability
from gambit.pq_types import Contract, ContractGroup, MarketOrder
from gambit.risk import DecisionStatus, InstrumentTradabilityPolicy, RiskContext, decide_order
from gambit.strategy import Strategy

TIMESTAMP = np.datetime64("2024-01-02")


def _price(_contract, _timestamps, _index, _context):
    return 100.0


def _context(contract, open_orders=()):
    strategy = Strategy(np.array([TIMESTAMP]), [contract.contract_group], _price)
    return RiskContext(strategy.account, TIMESTAMP, open_orders)


def test_contract_retains_validated_instrument_metadata() -> None:
    spec = InstrumentSpec(
        asset_class=AssetClass.FUTURE,
        currency="usd",
        tick_size=0.25,
        exchange_calendar="CME_Equity",
        trading_timezone="America/Chicago",
        liquidity_group="equity-index",
    )

    contract = Contract.create("ESH4", instrument_spec=spec, multiplier=50)

    assert contract.instrument_spec.currency == "USD"
    assert contract.instrument_spec.tick_size == 0.25
    assert contract.multiplier == 50


@pytest.mark.parametrize("multiplier", [0, -1, np.inf, True])
def test_contract_rejects_invalid_multiplier_without_registration(multiplier) -> None:
    symbol = f"INVALID-MULTIPLIER-{multiplier}"

    with pytest.raises(ValueError, match="contract multiplier"):
        Contract.create(symbol, multiplier=multiplier)

    assert Contract.get(symbol) is None


def test_contract_owns_validated_component_snapshot() -> None:
    first = Contract.create("BASKET-FIRST")
    components = [(first, 1)]

    basket = Contract.create("BASKET-SNAPSHOT", components=components)
    components.append((Contract.create("BASKET-SECOND"), -1))

    assert basket.components == [(first, 1.0)]


def test_contract_rejects_invalid_component_without_registration() -> None:
    component = Contract.create("INVALID-BASKET-COMPONENT")

    with pytest.raises(ValueError, match="component ratios must be nonzero"):
        Contract.create("INVALID-BASKET", components=[(component, 0)])

    assert Contract.get("INVALID-BASKET") is None


@pytest.mark.parametrize("name", ["", None, 1])
def test_contract_group_rejects_invalid_names_without_registration(name) -> None:
    before = dict(ContractGroup._instances)

    with pytest.raises(ValueError, match="group name"):
        ContractGroup.get(name)

    assert ContractGroup._instances == before


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("properties", {}, "properties"),
        ("instrument_spec", object(), "instrument_spec"),
    ],
)
def test_contract_rejects_invalid_metadata_without_registration(field, value, message) -> None:
    symbol = f"INVALID-{field.upper()}"

    with pytest.raises(TypeError, match=message):
        Contract.create(symbol, **{field: value})

    assert Contract.get(symbol) is None


def test_duplicate_metadata_requires_a_canonical_symbol() -> None:
    with pytest.raises(ValueError, match="duplicate_of"):
        InstrumentSpec(tradability=Tradability.DUPLICATE)


def test_tradability_policy_rejects_new_untradeable_exposure() -> None:
    spec = InstrumentSpec(tradability=Tradability.UNTRADEABLE)
    contract = Contract.create("BLOCKED", instrument_spec=spec)
    order = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=1)

    decision = decide_order(order, _context(contract), [InstrumentTradabilityPolicy()])

    assert decision.status is DecisionStatus.REJECTED
    assert decision.code == "instrument_untradeable"


def test_tradability_policy_allows_reducing_ignored_position() -> None:
    group = ContractGroup.get("ignored")
    spec = InstrumentSpec(tradability=Tradability.IGNORED)
    contract = Contract.create("IGNORED", group, instrument_spec=spec)
    existing_buy = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=5)
    reducing_sell = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=-2)

    decision = decide_order(
        reducing_sell,
        _context(contract, [existing_buy]),
        [InstrumentTradabilityPolicy()],
    )

    assert decision.status is DecisionStatus.ACCEPTED


def test_tradability_policy_rejects_expired_contract() -> None:
    contract = Contract.create("EXPIRED", expiry=np.datetime64("2024-01-01"))
    order = MarketOrder(contract=contract, timestamp=TIMESTAMP, qty=-1)

    decision = decide_order(order, _context(contract), [InstrumentTradabilityPolicy()])

    assert decision.status is DecisionStatus.REJECTED
    assert decision.code == "contract_expired"
