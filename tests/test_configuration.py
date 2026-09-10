from datetime import datetime, timezone

import numpy as np
import polars as pl
import pytest

from gambit.configuration import RunConfiguration, RunProvenance, fingerprint_polars_frame, load_run_configuration


def test_configuration_layers_are_typed_and_deterministic() -> None:
    first = RunConfiguration.from_layers({"trade_lag": 1}, {"starting_equity": 2_000.0})
    second = RunConfiguration.from_layers({"starting_equity": 2_000.0, "trade_lag": 1})

    assert first == second
    assert first.digest == second.digest
    with pytest.raises(ValueError, match="unknown"):
        RunConfiguration.from_layers({"mystery": True})


def test_yaml_configuration_is_optional_and_layered(tmp_path) -> None:
    user_file = tmp_path / "user.yml"
    user_file.write_text("trade_lag: 2\nstarting_equity: 5000\n")

    config = load_run_configuration(
        tmp_path / "missing.yml",
        user_file,
        defaults={"log_orders": False},
        overrides={"trade_lag": 3},
    )

    assert config.trade_lag == 3
    assert config.starting_equity == 5_000


def test_frame_fingerprint_captures_values_order_and_schema() -> None:
    frame = pl.DataFrame({"symbol": ["A", "B"], "price": [1.0, 2.0]})

    assert fingerprint_polars_frame(frame) == fingerprint_polars_frame(frame.clone())
    assert fingerprint_polars_frame(frame) != fingerprint_polars_frame(frame.reverse())
    assert fingerprint_polars_frame(frame) != fingerprint_polars_frame(frame.with_columns(pl.col("price").cast(pl.Float32)))


def test_run_fingerprint_excludes_capture_time_but_includes_inputs() -> None:
    config = RunConfiguration(trade_lag=1)
    first = RunProvenance(config, package_version="1.0.2", git_commit="abc", captured_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    second = RunProvenance(config, package_version="1.0.2", git_commit="abc", captured_at=datetime(2025, 1, 1, tzinfo=timezone.utc))

    assert first.run_fingerprint == second.run_fingerprint
    updated = first.with_input("prices", "123")
    assert updated.run_fingerprint != first.run_fingerprint
    assert dict(first.input_fingerprints) == {}
    assert updated.snapshot()["configuration_digest"] == config.digest
    assert updated.snapshot()["input_fingerprints"] == {"prices": "123"}


@pytest.mark.parametrize("field", ["trade_lag", "pnl_calc_time"])
@pytest.mark.parametrize("value", [True, False, 0.5, 1.0, float("nan"), float("inf"), "1", None])
def test_integer_configuration_fields_reject_wrong_types(field, value):
    with pytest.raises(TypeError, match=field):
        RunConfiguration(**{field: value})


@pytest.mark.parametrize("field", ["run_final_calc", "log_orders", "log_trades"])
@pytest.mark.parametrize("value", [0, 1, "false", "true", None, np.bool_(True)])
def test_boolean_configuration_fields_require_actual_booleans(field, value):
    with pytest.raises(TypeError, match=field):
        RunConfiguration(**{field: value})


@pytest.mark.parametrize("value", [True, "100", None, 1j])
def test_equity_configuration_rejects_wrong_types(value):
    with pytest.raises(TypeError, match="starting_equity"):
        RunConfiguration(starting_equity=value)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 10**1000])
def test_equity_configuration_rejects_invalid_numbers(value):
    with pytest.raises(ValueError, match="starting_equity"):
        RunConfiguration(starting_equity=value)


def test_configuration_normalizes_numpy_numbers_for_json():
    configuration = RunConfiguration(starting_equity=np.float32(100), trade_lag=np.int64(1), pnl_calc_time=np.int32(2))
    assert configuration.digest == RunConfiguration(starting_equity=100.0, trade_lag=1, pnl_calc_time=2).digest


@pytest.mark.parametrize("text", ["trade_lag: 1\ntrade_lag: 2\n", "trade_lag: .nan\n", 'log_orders: "false"\n', "1: 2\n"])
def test_yaml_configuration_rejects_ambiguous_or_untyped_values(tmp_path, text):
    path = tmp_path / "invalid.yml"
    path.write_text(text)
    with pytest.raises((ValueError, TypeError)):
        load_run_configuration(path)


@pytest.mark.parametrize("layer", [[("trade_lag", 1)], {1: 2}])
def test_configuration_layers_require_string_keyed_mappings(layer):
    with pytest.raises(TypeError, match="mappings"):
        RunConfiguration.from_layers(layer)


@pytest.mark.parametrize("manifest", ['[]', '{"version":true}', '{"version":2}', '{"version":1,"value":NaN}'])
def test_execution_manifest_rejects_invalid_format(manifest):
    with pytest.raises(ValueError):
        RunProvenance(RunConfiguration(), execution_manifest_json=manifest)
