from datetime import datetime, timezone

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
