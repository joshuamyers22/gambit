"""Run an executable target through Strategy with pending-order reservation."""

from types import SimpleNamespace

import numpy as np
import polars as pl

import gambit

timestamps = np.array(
    [
        np.datetime64("2026-08-28T16:00"),
        np.datetime64("2026-08-28T16:01"),
        np.datetime64("2026-08-28T16:02"),
    ]
)
group = gambit.ContractGroup.get("target-strategy-example")
contract = gambit.Contract.create("TARGET-ASSET", group)


def price(_contract, _timestamps, _index, _context):
    return 100.0


def target_inputs(timestamp, _account, _strategy_context):
    return gambit.ExecutableTargetInputs(
        exposures=pl.DataFrame(
            {"symbol": [contract.symbol], "currency": ["USD"], "net_exposure": [300.0]}
        ),
        prices=pl.DataFrame(
            {
                "symbol": [contract.symbol],
                "currency": ["USD"],
                "price": [100.0],
                "as_of": np.array([timestamp], dtype="datetime64[ns]"),
            }
        ),
        fx=gambit.FxRateSnapshot("USD", timestamp, {}),
        calculation=gambit.CalculationContext(timestamp, base_currency="USD"),
    )


position_limit = gambit.MaxPositionQuantity(3)
target_rule = gambit.ExecutableTargetRule(
    gambit.ExecutableTargetBuilder({contract.symbol: gambit.TradableUnitRule()}),
    [contract],
    target_inputs,
    risk_measures=[gambit.NetExposureMeasure()],
    risk_policies=[position_limit],
)
strategy = gambit.Strategy(
    timestamps,
    [group],
    price,
    trade_lag=2,
    strategy_context=SimpleNamespace(),
)
strategy.add_signal("target", lambda *_args: np.ones(3, dtype=bool))
strategy.add_rule("target", target_rule, signal_name="target")
# Register the same controls at final Strategy admission against live state.
strategy.add_risk_policy(position_limit)
strategy.add_market_sim(gambit.SimpleMarketSimulator(price))
strategy.run()

assert len(strategy.orders()) == 1
assert strategy.order_decisions[0].snapshot.qty == 3
assert [(trade.timestamp, trade.qty) for trade in strategy.trades()] == [(timestamps[2], 3)]
assert target_rule.latest_result.positions[0, "pending_quantity"] == 3
assert target_rule.latest_result.orders == ()
print(target_rule.latest_result.positions)
