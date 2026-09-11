from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Sequence

import numpy as np
import polars as pl
import pytest

from gambit.backtest_result import BacktestBundleError, BacktestResult
from gambit.market_data import validate_market_data
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Order, Trade
from gambit.risk_measures import NetExposureMeasure
from gambit.risk_reporting import StressScenario
from gambit.strategy import Strategy, StrategyContextType


def _strategy(*, reject: bool = False) -> Strategy:
    Contract.clear_cache()
    ContractGroup.clear_cache()
    group = ContractGroup.get("TEST")
    contract = Contract.create("TEST", contract_group=group)
    timestamps = np.array(["2024-01-01", "2024-01-02"], dtype="datetime64[D]")

    def price(*args: object) -> float:
        return 10.0

    def signal(*args: object) -> np.ndarray:
        return np.array([True, False])

    def rule(
        contract_group: ContractGroup,
        i: int,
        timestamps: np.ndarray,
        indicators: SimpleNamespace,
        signal: np.ndarray,
        account: object,
        orders: Sequence[Order],
        context: StrategyContextType,
    ) -> list[Order]:
        del contract_group, indicators, signal, account, orders, context
        return [MarketOrder(contract=contract, timestamp=timestamps[i], qty=1, reason_code="test")]

    def simulator(
        orders: Sequence[Order], i: int, timestamps: np.ndarray, *args: object
    ) -> list[Trade]:
        trades = [Trade(order.contract, order, timestamps[i], order.qty, 10.0) for order in orders]
        for order in orders:
            order.fill()
        return trades

    strategy = Strategy(timestamps, [group], price, run_final_calc=True)
    strategy.add_signal("always", signal)
    strategy.add_rule("enter", rule, "always")
    strategy.add_market_sim(simulator)
    if reject:
        from gambit.risk import MaxOrderQuantity

        strategy.add_risk_policy(MaxOrderQuantity(0.5))
    return strategy


def test_run_records_actual_pre_run_settings_and_policy_parameters():
    from gambit.risk import MaxOrderQuantity

    strategy = _strategy()
    original = strategy.capture_execution_provenance()
    strategy.trade_lag = 1
    strategy.add_risk_policy(MaxOrderQuantity(2))
    result = strategy.run()
    assert result.provenance.configuration.trade_lag == 1
    assert result.provenance.run_fingerprint != original.run_fingerprint
    manifest = result.provenance.snapshot()["execution_manifest"]
    policies = [component for component in manifest["components"] if component["role"] == "risk"]
    assert policies[0]["description"]["parameters"]["maximum"] == 2
    assert manifest["unresolved_scope"]
    assert original.configuration.trade_lag == 0
    manifest["components"].clear()
    assert result.provenance.snapshot()["execution_manifest"]["components"]


def test_execution_identity_changes_with_policy_order_and_parameters():
    from gambit.risk import MaxOrderQuantity, MaxPositionQuantity

    strategy = _strategy()
    strategy.add_risk_policy(MaxOrderQuantity(2))
    strategy.add_risk_policy(MaxPositionQuantity(3))
    first = strategy.capture_execution_provenance().run_fingerprint
    assert first == strategy.capture_execution_provenance().run_fingerprint
    strategy.risk_policies.reverse()
    second = strategy.capture_execution_provenance().run_fingerprint
    assert first != second
    strategy.risk_policies[-1] = MaxOrderQuantity(4)
    assert second != strategy.capture_execution_provenance().run_fingerprint


def test_invalid_runtime_settings_fail_before_any_orders():
    strategy = _strategy()
    strategy.trade_lag = float("nan")
    with pytest.raises(TypeError, match="trade_lag"):
        strategy.run()
    assert strategy.account.trade_count == 0


def test_strategy_passes_normalized_configuration_to_account():
    strategy = Strategy(np.array(["2024-01-01"], dtype="datetime64[D]"), [ContractGroup.get("normalized")],
                        lambda *args: 10.0, starting_equity=np.float32(100), pnl_calc_time=np.int64(10))
    assert strategy.account.starting_equity == 100.0
    assert strategy.capture_execution_provenance().configuration.digest


def test_configuration_mutation_during_callback_cannot_publish_success():
    strategy = _strategy()
    original = strategy.run_signals

    def mutate():
        original()
        strategy.trade_lag = 1

    strategy.run_signals = mutate
    with pytest.raises(RuntimeError, match="changed during the run"):
        strategy.run()
    assert strategy.account.trade_count == 0
    assert not strategy._running


def test_registration_mutation_during_callback_cannot_publish_success():
    from gambit.risk import MaxOrderQuantity

    strategy = _strategy()
    original = strategy.run_signals

    def mutate():
        original()
        strategy.risk_policies.append(MaxOrderQuantity(2))

    strategy.run_signals = mutate
    with pytest.raises(RuntimeError, match="registration"):
        strategy.run()
    assert strategy.account.trade_count == 0


def test_accounting_configuration_drift_requires_new_strategy():
    strategy = _strategy()
    strategy.account.starting_equity = 5
    with pytest.raises(ValueError, match="accounting configuration changed"):
        strategy.run()


def test_legacy_v2_bundle_without_execution_manifest_remains_readable(tmp_path):
    strategy = _strategy()
    result = strategy.run()
    legacy_provenance = replace(result.provenance, execution_manifest_json=None)
    path = tmp_path / "legacy"
    result.save(path)
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = 2
    manifest["provenance"] = legacy_provenance.snapshot()
    manifest_path.write_text(json.dumps(manifest))
    restored = BacktestResult.load(path)
    assert restored.provenance.snapshot() == legacy_provenance.snapshot()


def test_execution_manifest_tampering_is_detected(tmp_path):
    result = _strategy().run()
    path = tmp_path / "tampered"
    result.save(path)
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["version"] == 4
    manifest["provenance"]["execution_manifest"]["components"] = []
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(BacktestBundleError, match="provenance fingerprint"):
        BacktestResult.load(path)


def test_run_returns_detached_result_and_telemetry() -> None:
    strategy = _strategy()
    result = strategy.run()

    assert isinstance(result, BacktestResult)
    assert result.telemetry.timestamps_processed == 2
    assert result.telemetry.orders_proposed == 1
    assert result.telemetry.orders_accepted == 1
    assert result.telemetry.orders_filled == 1
    assert result.telemetry.trades_executed == 1
    assert [stage.name for stage in result.telemetry.stages] == [
        "validation",
        "indicators",
        "signals",
        "rules_execution_accounting",
    ]
    assert result.telemetry.elapsed_seconds >= 0
    assert result.orders["status"].to_list() == ["filled"]
    assert result.provenance.run_fingerprint == strategy.provenance.run_fingerprint

    changed = result.orders.with_columns(pl.lit("changed").alias("status"))
    assert changed["status"].to_list() == ["changed"]
    assert result.orders["status"].to_list() == ["filled"]


def test_result_captures_rejected_order_decision() -> None:
    result = _strategy(reject=True).run()

    assert result.telemetry.orders_rejected == 1
    assert result.telemetry.orders_accepted == 0
    assert result.telemetry.trades_executed == 0
    assert result.decisions.row(0, named=True)["code"] == "order_quantity_exceeded"
    assert result.orders["status"].to_list() == ["cancelled"]


def test_result_bundle_round_trip(tmp_path: Path) -> None:
    destination = tmp_path / "run.gambit"
    result = _strategy().run()

    assert result.save(destination) == destination
    restored = BacktestResult.load(destination)

    assert restored.provenance.snapshot() == result.provenance.snapshot()
    assert restored.telemetry == result.telemetry
    for name, frame in result.frames.items():
        assert restored.frames[name].equals(frame)
    with pytest.raises(FileExistsError):
        result.save(destination)


@pytest.mark.parametrize("reject", [False, True])
def test_result_serializes_decision_snapshot_not_later_order_state(tmp_path, reject):
    strategy = _strategy(reject=reject)
    original = strategy.run()
    order = strategy.order_decisions[0].order
    order.contract = Contract.create("CHANGED", order.contract.contract_group)
    order.timestamp += np.timedelta64(1, "D")
    order.qty = 99
    order.reason_code = "edited after decision"
    changed = strategy._backtest_result(())
    assert changed.decisions.equals(original.decisions)
    assert changed.decisions["symbol"].to_list() == ["TEST"]
    assert changed.decisions["order_status"].to_list() == ["open"]
    assert changed.decisions["proposed_qty"].to_list() == [1.]
    assert changed.decisions["order_type"].to_list() == ["MarketOrder"]
    assert changed.decisions["reason_code"].to_list() == ["test"]
    path = changed.save(tmp_path / "snapshot")
    assert json.loads((path / "manifest.json").read_text())["version"] == 4
    assert BacktestResult.load(path).decisions.equals(original.decisions)


@pytest.mark.parametrize("version", [2, 3])
def test_legacy_decision_schema_is_not_backfilled_with_invented_snapshots(tmp_path, version):
    result = _strategy().run()
    path = result.save(tmp_path / "legacy-decisions")
    columns = ["symbol", "timestamp", "status", "policy", "code", "message", "proposed_qty"]
    legacy = result.decisions.select(columns)
    frame_path = path / "decisions.arrow"
    legacy.write_ipc(frame_path, compression="uncompressed")
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = version
    metadata = manifest["frames"]["decisions"]
    metadata["sha256"] = hashlib.sha256(frame_path.read_bytes()).hexdigest()
    metadata["schema"] = [{"name": name, "dtype": str(dtype)} for name, dtype in legacy.schema.items()]
    manifest_path.write_text(json.dumps(manifest))
    restored = BacktestResult.load(path)
    assert restored.decisions.equals(legacy)
    # Re-saving a legacy result preserves unavailable fields as absent too.
    assert BacktestResult.load(restored.save(tmp_path / "resaved")).decisions.equals(legacy)


def test_result_bundle_is_byte_deterministic(tmp_path: Path) -> None:
    result = _strategy().run()
    first = result.save(tmp_path / "first")
    second = result.save(tmp_path / "second")

    for filename in ["manifest.json", *(f"{name}.arrow" for name in result.frames)]:
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_result_includes_only_explicitly_requested_analytics(tmp_path: Path) -> None:
    strategy = _strategy()
    timestamp = np.datetime64("2024-01-02")
    strategy.request_risk_result("closing-risk", timestamp, [NetExposureMeasure()])
    strategy.request_risk_report(
        "closing-stress",
        timestamp,
        scenarios=[StressScenario("down-10", {"*": -0.1})],
    )
    validation = validate_market_data(
        pl.DataFrame(
            {
                "timestamp": np.array(["2024-01-02"], dtype="datetime64[ns]"),
                "price": [-1.0],
            }
        ),
        now=timestamp,
    )
    strategy.record_market_data_validation("prices", validation)
    valid = validate_market_data(
        pl.DataFrame(
            {
                "timestamp": np.array(["2024-01-02"], dtype="datetime64[ns]"),
                "price": [10.0],
            }
        ),
        now=timestamp,
    )
    strategy.record_market_data_validation("valid-prices", valid)

    result = strategy.run()

    assert result.telemetry.stage("requested_analytics").units == 2
    assert result.risk_measures["artifact"].unique().to_list() == ["closing-risk"]
    assert result.risk_exposures["artifact"].unique().to_list() == ["closing-stress"]
    assert result.stress_results.row(0, named=True)["scenario"] == "down-10"
    assert result.validation_findings.filter(pl.col("artifact") == "prices")["code"].to_list() == [
        "non_positive_prices"
    ]
    valid_row = result.validation_findings.filter(pl.col("artifact") == "valid-prices").row(0, named=True)
    assert valid_row["is_valid"] is True
    assert valid_row["code"] is None

    restored = BacktestResult.load(result.save(tmp_path / "analytics.gambit"))
    assert restored.risk_measures.equals(result.risk_measures)
    assert restored.risk_exposures.equals(result.risk_exposures)
    assert restored.stress_results.equals(result.stress_results)
    assert restored.validation_findings.equals(result.validation_findings)


def test_result_bundle_rejects_corrupted_frame(tmp_path: Path) -> None:
    destination = tmp_path / "run.gambit"
    _strategy().run().save(destination)
    with (destination / "trades.arrow").open("ab") as output:
        output.write(b"corrupt")

    with pytest.raises(BacktestBundleError, match="checksum mismatch"):
        BacktestResult.load(destination)


def test_result_bundle_rejects_manifest_filename_substitution(tmp_path: Path) -> None:
    destination = tmp_path / "run.gambit"
    _strategy().run().save(destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["frames"]["trades"]["file"] = "../outside.arrow"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(BacktestBundleError, match="invalid filename"):
        BacktestResult.load(destination)
