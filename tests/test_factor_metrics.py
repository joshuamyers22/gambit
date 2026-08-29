from __future__ import annotations

import json
import multiprocessing

import pytest

from gambit.factor_metrics import (
    COUNTER_NAMES,
    FactorMetricsError,
    format_prometheus_metrics,
    read_factor_cache_metrics,
    record_factor_cache_metrics,
)


def _increment(root: str, repeats: int) -> None:
    for _ in range(repeats):
        record_factor_cache_metrics(root, cache_hits=1)


def test_factor_metrics_are_atomic_and_fixed_cardinality(tmp_path) -> None:
    first = record_factor_cache_metrics(tmp_path, cache_hits=2, reclaimed_bytes=4096)
    second = record_factor_cache_metrics(tmp_path, cache_hits=3)

    assert first.counters["cache_hits"] == 2
    assert second.counters["cache_hits"] == 5
    assert second.counters["reclaimed_bytes"] == 4096
    assert tuple(second.counters) == COUNTER_NAMES


def test_factor_metrics_reject_invalid_updates(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown"):
        record_factor_cache_metrics(tmp_path, arbitrary_node_key=1)
    with pytest.raises(ValueError, match="non-negative"):
        record_factor_cache_metrics(tmp_path, cache_hits=-1)


def test_factor_metrics_do_not_overwrite_corrupt_state(tmp_path) -> None:
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    path = metrics / "lifetime.json"
    path.write_text('{"version": 999}')

    with pytest.raises(FactorMetricsError, match="invalid"):
        record_factor_cache_metrics(tmp_path, cache_hits=1)

    assert json.loads(path.read_text()) == {"version": 999}


def test_factor_metrics_upgrade_v1_counters_without_losing_values(tmp_path) -> None:
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    legacy = {name: index for index, name in enumerate(COUNTER_NAMES[:8])}
    (metrics / "lifetime.json").write_text(
        json.dumps(
            {
                "format": "gambit-factor-cache-metrics",
                "version": 1,
                "updated_ns": 1,
                "counters": legacy,
            }
        )
    )

    upgraded = record_factor_cache_metrics(tmp_path, migration_nodes=1)

    assert upgraded.version == 2
    assert all(upgraded.counters[name] == value for name, value in legacy.items())
    assert upgraded.counters["migration_nodes"] == 1


def test_factor_metrics_lock_prevents_lost_cross_process_updates(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_increment, args=(str(tmp_path), 20)) for _ in range(3)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    assert read_factor_cache_metrics(tmp_path).counters["cache_hits"] == 60


def test_prometheus_metrics_have_bounded_names(tmp_path) -> None:
    metrics = record_factor_cache_metrics(tmp_path, cache_misses=7)
    text = format_prometheus_metrics(metrics)

    assert "gambit_factor_cache_misses_total 7\n" in text
    assert text.endswith("\n")
    assert len([line for line in text.splitlines() if line.startswith("gambit_")]) == len(COUNTER_NAMES)
    assert format_prometheus_metrics(metrics, openmetrics=True).endswith("# EOF\n")
