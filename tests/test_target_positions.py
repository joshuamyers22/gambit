from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest

from gambit.account import Account
from gambit.calculation import CalculationContext
from gambit.covariance_risk import CovarianceEstimate, PortfolioVolatilityMeasure
from gambit.currency import FxRateSnapshot
from gambit.instruments import InstrumentSpec
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Trade
from gambit.risk_measures import NetExposureMeasure
from gambit.target_positions import ExecutableTargetBuilder, TargetRounding, TradableUnitRule

pytestmark = pytest.mark.acceptance

TIMESTAMP = np.datetime64("2026-09-12T16:00")


def _price(_contract, _timestamps, _index, _context):
    return 100.0


def _fixture(prefix: str = "target"):
    group = ContractGroup.get(f"{prefix}-group")
    usd = Contract.create(
        f"{prefix}-USD", group, instrument_spec=InstrumentSpec(currency="USD")
    )
    eur = Contract.create(
        f"{prefix}-EUR",
        group,
        multiplier=10,
        instrument_spec=InstrumentSpec(currency="EUR"),
    )
    account = Account([group], np.array([TIMESTAMP]), _price, SimpleNamespace())
    account.add_trades(
        [
            Trade(usd, MarketOrder(contract=usd, timestamp=TIMESTAMP, qty=3), TIMESTAMP, 3, 100.0),
            Trade(eur, MarketOrder(contract=eur, timestamp=TIMESTAMP, qty=-2), TIMESTAMP, -2, 50.0),
        ]
    )
    exposures = pl.DataFrame(
        {
            "symbol": [usd.symbol, eur.symbol],
            "currency": ["USD", "USD"],
            "net_exposure": [1_050.0, 1_200.0],
        }
    )
    prices = pl.DataFrame(
        {
            "symbol": [usd.symbol, eur.symbol],
            "currency": ["USD", "EUR"],
            "price": [100.0, 50.0],
            "as_of": np.array([TIMESTAMP, TIMESTAMP], dtype="datetime64[ns]"),
        }
    )
    fx = FxRateSnapshot("USD", TIMESTAMP, {"EUR": 1.2}, source="closing-fix")
    context = CalculationContext(TIMESTAMP, base_currency="USD")
    builder = ExecutableTargetBuilder(
        {
            usd.symbol: TradableUnitRule(),
            eur.symbol: TradableUnitRule(lot_size=2),
        }
    )
    return builder, exposures, [usd, eur], prices, fx, context, account


def test_targets_reconcile_fx_multiplier_holdings_pending_and_lots() -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture("reconcile")
    pending = MarketOrder(contract=contracts[0], timestamp=TIMESTAMP, qty=2)

    result = builder.build(
        exposures, contracts, prices, fx, context, account, pending_orders=[pending]
    )

    assert result.positions.select(
        "base_unit_notional",
        "raw_target_quantity",
        "target_quantity",
        "current_quantity",
        "pending_quantity",
        "projected_quantity",
        "order_quantity",
        "achieved_net_exposure",
        "tracking_error",
    ).rows() == [
        (100.0, 10.5, 11, 3, 2, 5, 6, 1_100.0, 50.0),
        (600.0, 2.0, 2, -2, 0, -2, 4, 1_200.0, 0.0),
    ]
    assert [(order.contract.symbol, order.qty, order.reason_code) for order in result.orders] == [
        (contracts[0].symbol, 6, "portfolio_target"),
        (contracts[1].symbol, 4, "portfolio_target"),
    ]


def test_repeated_target_accounts_for_every_still_open_order() -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture("pending")
    existing = MarketOrder(contract=contracts[0], timestamp=TIMESTAMP, qty=2)
    existing.request_cancel()
    first = builder.build(
        exposures, contracts, prices, fx, context, account, pending_orders=[existing]
    )

    second = builder.build(
        exposures,
        contracts,
        prices,
        fx,
        context,
        account,
        pending_orders=[existing, *first.orders],
    )

    assert second.orders == ()
    assert second.positions["order_quantity"].to_list() == [0, 0]


def test_rounding_is_deterministic_for_sign_ties_and_lots() -> None:
    nearest = TradableUnitRule(lot_size=2, rounding=TargetRounding.NEAREST)
    toward_zero = TradableUnitRule(lot_size=2, rounding=TargetRounding.TOWARD_ZERO)

    assert nearest.round(1.0) == 2
    assert nearest.round(-1.0) == -2
    assert nearest.round(4.9) == 4
    assert toward_zero.round(3.9) == 2
    assert toward_zero.round(-3.9) == -2

    with pytest.raises(ValueError, match="platform integer"):
        nearest.round(np.finfo(float).max)


def test_no_trade_band_reduces_controlled_target_oscillation() -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture("buffer")
    buffered = ExecutableTargetBuilder(
        {
            contracts[0].symbol: TradableUnitRule(no_trade_band=100.0),
            contracts[1].symbol: TradableUnitRule(lot_size=2),
        }
    )
    low = exposures.with_columns(
        pl.Series("net_exposure", [249.0, -1_200.0], dtype=pl.Float64)
    )
    high = exposures.with_columns(
        pl.Series("net_exposure", [351.0, -1_200.0], dtype=pl.Float64)
    )

    unbuffered_orders = sum(
        len(builder.build(target, contracts, prices, fx, context, account).orders)
        for target in (low, high)
    )
    buffered_results = [
        buffered.build(target, contracts, prices, fx, context, account)
        for target in (low, high)
    ]

    assert unbuffered_orders == 2
    assert sum(len(result.orders) for result in buffered_results) == 0
    assert buffered_results[0].positions.select(
        "no_trade_band",
        "rounded_target_exposure",
        "projected_net_exposure",
        "unbuffered_order_quantity",
        "inside_no_trade_band",
        "buffer_applied",
        "order_quantity",
        "post_order_quantity",
        "achieved_net_exposure",
        "tracking_error",
    ).row(0) == (100.0, 200.0, 300.0, -1, True, True, 0, 3, 300.0, 51.0)
    assert buffered_results[1].positions[0, "tracking_error"] == -51.0


def test_no_trade_band_uses_pending_projection_but_allows_material_reduction() -> None:
    _builder, exposures, contracts, prices, fx, context, account = _fixture("buffer-pending")
    builder = ExecutableTargetBuilder(
        {
            contracts[0].symbol: TradableUnitRule(no_trade_band=100.0),
            contracts[1].symbol: TradableUnitRule(lot_size=2),
        }
    )
    target = exposures.with_columns(
        pl.Series("net_exposure", [451.0, -1_200.0], dtype=pl.Float64)
    )
    pending = MarketOrder(contract=contracts[0], timestamp=TIMESTAMP, qty=1)

    buffered = builder.build(
        target, contracts, prices, fx, context, account, pending_orders=[pending]
    )
    reduction = builder.build(
        target.with_columns(
            pl.when(pl.col("symbol") == contracts[0].symbol)
            .then(0.0)
            .otherwise(pl.col("net_exposure"))
            .alias("net_exposure")
        ),
        contracts,
        prices,
        fx,
        context,
        account,
    )

    assert buffered.positions.select(
        "pending_quantity",
        "projected_quantity",
        "unbuffered_order_quantity",
        "buffer_applied",
        "order_quantity",
    ).row(0) == (1, 4, 1, True, 0)
    assert [(order.contract.symbol, order.qty) for order in reduction.orders] == [
        (contracts[0].symbol, -3)
    ]


def test_achieved_exposures_and_risk_follow_rounding_and_buffering() -> None:
    _builder, exposures, contracts, prices, fx, context, account = _fixture("achieved-risk")
    estimate = CovarianceEstimate(
        tuple(contract.symbol for contract in contracts),
        np.diag([0.04, 0.01]),
        TIMESTAMP,
        observations=60,
        annualization_factor=252.0,
    )
    builder = ExecutableTargetBuilder(
        {
            contracts[0].symbol: TradableUnitRule(no_trade_band=100.0),
            contracts[1].symbol: TradableUnitRule(lot_size=2),
        }
    )
    buffered_targets = exposures.with_columns(
        pl.Series("net_exposure", [249.0, 1_200.0], dtype=pl.Float64)
    )

    result = builder.build(
        buffered_targets,
        contracts,
        prices,
        fx,
        context,
        account,
        risk_measures=[NetExposureMeasure(), PortfolioVolatilityMeasure(estimate)],
    )

    assert result.exposures.select(
        "symbol", "currency", "price", "quantity", "net_exposure", "gross_exposure"
    ).rows() == [
        (contracts[0].symbol, "USD", 100.0, 3, 300.0, 300.0),
        (contracts[1].symbol, "USD", 60.0, 2, 1_200.0, 1_200.0),
    ]
    assert result.risk is not None
    assert result.risk.filter(measure="net_exposure").aggregate()[0, "value"] == 1_500.0
    assert result.risk.filter(measure="portfolio_volatility").data[0, "value"] == pytest.approx(
        np.hypot(300.0 * 0.2, 1_200.0 * 0.1)
    )


def test_achieved_exposure_and_risk_results_are_detached() -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture("risk-ownership")
    result = builder.build(
        exposures,
        contracts,
        prices,
        fx,
        context,
        account,
        risk_measures=[NetExposureMeasure()],
    )

    achieved = result.exposures
    achieved[0, "net_exposure"] = -1.0
    assert result.risk is not None
    risk = result.risk
    risk.data[0, "value"] = -1.0

    assert result.exposures[0, "net_exposure"] == 1_100.0
    assert result.risk is not None
    assert result.risk.data[0, "value"] == 1_100.0


def test_achieved_risk_rejects_future_models_and_invalid_measure_collections() -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture("risk-cutoff")
    future = TIMESTAMP + np.timedelta64(1, "m")
    estimate = CovarianceEstimate(
        tuple(contract.symbol for contract in contracts),
        np.eye(2),
        future,
        observations=60,
        annualization_factor=252.0,
    )

    with pytest.raises(ValueError, match="risk measure.*after the calculation cutoff"):
        builder.build(
            exposures,
            contracts,
            prices,
            fx,
            context,
            account,
            risk_measures=[PortfolioVolatilityMeasure(estimate)],
        )
    with pytest.raises(TypeError, match="risk_measures must be a sequence"):
        builder.build(
            exposures,
            contracts,
            prices,
            fx,
            context,
            account,
            risk_measures="net_exposure",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("band", [True, "1", -1.0, np.nan, np.inf])
def test_no_trade_band_rejects_ambiguous_values(band: object) -> None:
    with pytest.raises((TypeError, ValueError), match="no_trade_band"):
        TradableUnitRule(no_trade_band=band)  # type: ignore[arg-type]


def test_small_target_rounds_to_zero_with_visible_tracking_error() -> None:
    builder, exposures, contracts, prices, fx, context, _account = _fixture("small")
    account = Account([contracts[0].contract_group], np.array([TIMESTAMP]), _price, SimpleNamespace())
    exposures = exposures.with_columns(pl.lit(40.0).alias("net_exposure"))

    result = builder.build(exposures, contracts, prices, fx, context, account)

    assert result.orders == ()
    assert result.positions["target_quantity"].to_list() == [0, 0]
    assert result.positions["tracking_error"].to_list() == [-40.0, -40.0]


@pytest.mark.parametrize("price", [0.0, -1.0, np.nan, np.inf])
def test_targets_reject_nonpositive_or_nonfinite_prices(price: float) -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture(f"price-{price}")
    prices = prices.with_columns(pl.lit(price).alias("price"))

    with pytest.raises(ValueError, match="finite and positive"):
        builder.build(exposures, contracts, prices, fx, context, account)


def test_targets_reject_lookahead_prices_and_fx() -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture("lookahead")
    future = TIMESTAMP + np.timedelta64(1, "m")

    with pytest.raises(ValueError, match="prices use market data after"):
        builder.build(
            exposures,
            contracts,
            prices.with_columns(pl.lit(future).alias("as_of")),
            fx,
            context,
            account,
        )
    with pytest.raises(ValueError, match="FX uses market data after"):
        builder.build(
            exposures,
            contracts,
            prices,
            FxRateSnapshot("USD", future, {"EUR": 1.2}),
            context,
            account,
        )


def test_targets_reject_missing_or_ambiguous_instrument_inputs() -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture("missing")

    with pytest.raises(ValueError, match="contracts must exactly match"):
        builder.build(exposures, contracts[:1], prices, fx, context, account)
    with pytest.raises(ValueError, match="prices must exactly match"):
        builder.build(exposures, contracts, prices.head(1), fx, context, account)
    with pytest.raises(ValueError, match="price currency does not match"):
        builder.build(
            exposures,
            contracts,
            prices.with_columns(pl.lit("USD").alias("currency")),
            fx,
            context,
            account,
        )
    with pytest.raises(ValueError, match="unit rules are missing"):
        ExecutableTargetBuilder({contracts[0].symbol: TradableUnitRule()}).build(
            exposures, contracts, prices, fx, context, account
        )
    with pytest.raises(ValueError, match="missing for EUR"):
        builder.build(
            exposures,
            contracts,
            prices,
            FxRateSnapshot("USD", TIMESTAMP, {}),
            context,
            account,
        )


def test_targets_reject_untranslated_duplicate_or_nonfinite_exposures() -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture("exposure")

    with pytest.raises(ValueError, match="base currency"):
        builder.build(
            exposures.with_columns(pl.lit("EUR").alias("currency")),
            contracts,
            prices,
            fx,
            context,
            account,
        )
    with pytest.raises(ValueError, match="one row per symbol"):
        builder.build(pl.concat([exposures, exposures.head(1)]), contracts, prices, fx, context, account)
    with pytest.raises(ValueError, match="must be finite"):
        builder.build(
            exposures.with_columns(pl.lit(np.inf).alias("net_exposure")),
            contracts,
            prices,
            fx,
            context,
            account,
        )


def test_target_results_own_positions_and_order_snapshots() -> None:
    builder, exposures, contracts, prices, fx, context, account = _fixture("ownership")
    result = builder.build(exposures, contracts, prices, fx, context, account)

    positions = result.positions
    positions[0, "target_quantity"] = 999
    order = result.orders[0]
    order.qty = 999

    assert result.positions[0, "target_quantity"] == 11
    assert result.orders[0].qty == 8
