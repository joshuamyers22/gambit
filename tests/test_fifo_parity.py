"""Triangulate the standalone reference, Python policy and native complete traces."""

import copy
import importlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from gambit.tick_backtest import TopOfBookBacktester

pytestmark = [pytest.mark.native, pytest.mark.skipif(
    os.name != "posix" or TopOfBookBacktester is None or not shutil.which("c++"),
    reason="POSIX, native extension and C++ compiler required")]


@pytest.fixture(scope="module")
def parity():
    sys.path.insert(0, str(Path(__file__).parents[1] / "benchmarks"))
    return importlib.import_module("fifo_parity")


@pytest.fixture(scope="module")
def executable(parity, tmp_path_factory):
    return parity.build_reference(tmp_path_factory.mktemp("fifo-reference") / "build")


def reference_result(parity, executable, tmp_path, events, config, chunk):
    from replay_contract import CONFIG

    defaults = dict(CONFIG, fee_ppm=0, latency_ns=0)
    defaults.update(config)
    with (tmp_path / "oracle-errors.txt").open("ab") as errors:
        reference = parity.Reference(executable, defaults, errors)
        try:
            for offset in range(0, len(events), chunk):
                reference.send(events[offset:offset + chunk])
            result = reference.snapshot()
            reference.close()
            return result
        finally:
            reference.abort()


@pytest.mark.parametrize("case,args", [
    ("test_manual_arrival_cancellation_ignored_partial_fifo_fills", ()),
    ("test_equal_timestamps_obey_sequence_and_opposing_price_only", ()),
    ("test_shared_cash_priority_is_global_sequence_not_instrument_id", ()),
    ("test_resting_price_and_queue_survive_top_changes_and_additions", ()),
    ("test_nonbest_and_crossing_arrivals_reject", (98, 101)),
    ("test_nonbest_and_crossing_arrivals_reject", (100, 101)),
    ("test_nonbest_and_crossing_arrivals_reject", (99, 99)),
    ("test_manual_sell_queue_and_fee_boundaries", (0,)),
    ("test_manual_sell_queue_and_fee_boundaries", (1,)),
    ("test_manual_sell_queue_and_fee_boundaries", (1000000,)),
    ("test_seeded_queue_trace_parity", (1, 0, 100)),
    ("test_seeded_queue_trace_parity", (19, 80, 1000000)),
    ("test_seeded_queue_trace_parity", (257, 10000, 1000000)),
])
def test_existing_hand_derived_and_python_policy_cases(parity, executable, tmp_path, monkeypatch, case, args):
    import test_fifo_backtest as policy

    original = policy.check

    def check_with_standalone(events, config, chunk=17):
        actual = original(events, config, chunk)
        expected = reference_result(parity, executable, tmp_path, events, config, 7)
        parity.compare_snapshot(actual, expected)
        return actual

    monkeypatch.setattr(policy, "check", check_with_standalone)
    getattr(policy, case)(*args)


@pytest.mark.parametrize("chunk", [1, 65521, 65536, 1048576])
def test_streaming_canonical_prefix_matches_python_and_native(parity, executable, tmp_path, chunk):
    from replay_contract import CONFIG
    from top_of_book_backtest import make_queue_events

    from test_fifo_backtest import python_fifo

    events = make_queue_events(0, 10003)
    config = dict(CONFIG, rebalance_events=16)
    expected = reference_result(parity, executable, tmp_path, events, config, chunk)
    python_config = {k: config[k] for k in ("instruments", "cash", "target_lots", "rebalance_events", "fee_ppm", "latency_ns")}
    python = python_fifo(events, **python_config)
    for key in parity.SCALARS:
        assert expected[key] == python[key]
    for key in ("positions", "orders", "fills", "queues"):
        assert expected[key].tolist() == python[key]
    engine = TopOfBookBacktester(**config)
    for offset in range(0, len(events), 19):
        engine.process_queue_batch(events[offset:offset + 19])
    parity.compare_snapshot(engine.result(), expected)


@pytest.fixture
def matched(parity, executable, tmp_path):
    from replay_contract import CONFIG
    from top_of_book_backtest import make_queue_events

    events = make_queue_events(0, 10003)
    config = dict(CONFIG, rebalance_events=16)
    engine = TopOfBookBacktester(**config)
    engine.process_queue_batch(events)
    return engine.result(), reference_result(parity, executable, tmp_path, events, config, 65521)


@pytest.mark.parametrize("array,field", [
    ("orders", "remaining"), ("orders", "status"), ("orders", "sequence"),
    ("fills", "fee"), ("fills", "quantity"), ("fills", "instrument_id"),
    ("queues", "ahead"), ("queues", "arrival_sequence"), ("queues", "limit_price"),
])
def test_comparator_detects_trace_corruption_despite_matching_terminal_state(parity, matched, array, field):
    actual, expected = matched
    damaged = copy.deepcopy(expected)
    damaged[array][field][-1] += 1
    with pytest.raises(AssertionError, match=field):
        parity.compare_snapshot(actual, damaged)


@pytest.mark.parametrize("field", ["cash", "equity", "total_fees", "net_pnl", "processed", "positions", "fills"])
def test_comparator_rejects_scalar_position_and_row_loss(parity, matched, field):
    actual, expected = matched
    damaged = copy.deepcopy(expected)
    if field == "fills":
        damaged[field] = damaged[field][:-1]
    elif field == "positions":
        damaged[field][0] += 1
    else:
        damaged[field] += 1
    with pytest.raises(AssertionError, match=field):
        parity.compare_snapshot(actual, damaged)


@pytest.mark.parametrize("target", ["cash", "positions", "queues"])
def test_comparator_rejects_float_coercion_even_when_values_appear_equal(parity, matched, target):
    actual, expected = matched
    changed = dict(actual)
    if target == "cash":
        changed[target] = float(actual[target])
    elif target == "positions":
        changed[target] = actual[target].astype(float)
    else:
        dtype = [(field, "<f8" if field == "arrival_sequence" else actual[target].dtype[field])
                 for field in actual[target].dtype.names]
        changed[target] = actual[target].astype(dtype)
    with pytest.raises(AssertionError, match=target):
        parity.compare_snapshot(changed, expected)


@pytest.mark.parametrize("field,value", [
    ("sequence", 9), ("event_time_ns", -1), ("receive_time_ns", -1),
    ("receive_time_ns", 2000000001), ("instrument_id", 8), ("flags", 1),
    ("bid", 0), ("ask", 1), ("bid_size", -1),
    ("trade_size", -1), ("trade_price", 0), ("aggressor", 2), ("reserved", 1),
])
def test_reference_rejects_malformed_events_without_success_snapshot(parity, executable, tmp_path, field, value):
    from replay_contract import CONFIG
    from top_of_book_backtest import make_queue_events

    events = make_queue_events(0, 4)
    target = events["book"] if field in events["book"].dtype.names else events
    target[field][1] = value
    with pytest.raises((RuntimeError, BrokenPipeError)):
        reference_result(parity, executable, tmp_path, events, CONFIG, 4)


@pytest.mark.parametrize("failure", ["capacity", "notional", "valuation"])
def test_reference_capacity_and_integer_range_fail_closed(parity, executable, tmp_path, failure):
    from replay_contract import CONFIG

    from test_fifo_backtest import make_events

    events = make_events(10)
    config = dict(CONFIG, instruments=1, cash=2**63 - 1, target_lots=3, rebalance_events=2, latency_ns=0)
    events["book"]["bid_size"] = 0
    events["aggressor"] = -1
    events["trade_price"] = 99
    events["trade_size"] = 3
    if failure == "capacity":
        config["audit_capacity"] = 1
    elif failure == "notional":
        events["book"]["bid"] = events["trade_price"] = 2**62
        events["book"]["ask"] = 2**62 + 1
    else:
        events["book"]["bid"][5:] = 2**62
        events["book"]["ask"][5:] = 2**62 + 1
    with pytest.raises((RuntimeError, BrokenPipeError)):
        reference_result(parity, executable, tmp_path, events, config, 3)


def test_truncated_wire_protocol_exits_with_failure(parity, executable, tmp_path):
    from replay_contract import CONFIG

    with (tmp_path / "errors").open("wb") as errors:
        reference = parity.Reference(executable, CONFIG, errors)
        reference.child.stdin.write(struct.pack("<Q", 2) + bytes(88))
        reference.child.stdin.close()
        assert reference.child.wait(timeout=5) == 2
        reference.abort()


def test_supervised_parity_retains_complete_arrays_and_checkpoint_proof(parity, executable, tmp_path):
    from replay_contract import workload

    trial = parity.run_trial(tmp_path / "run", workload("fifo-2m-dense-v1"), executable)
    assert trial["status"] == "exact_trace_match", trial
    assert trial["comparison"]["checkpoints"] == 2
    assert not trial["performance_qualification"]
    with np.load(tmp_path / "run/native-trace.npz", allow_pickle=False) as actual:
        with np.load(tmp_path / "run/reference-trace.npz", allow_pickle=False) as expected:
            parity.compare_snapshot(actual, expected)
    result = json.loads((tmp_path / "run/worker-result.json").read_text())
    assert result["canonical_controls_checked"]
    assert result["comparison"]["processed"] == 2000000
    assert result["resources"]["peak_rss_upper_bound_bytes"] < 4 * 1024**3


@pytest.mark.parametrize("budget", ["rss_bytes", "wall_seconds", "cpu_seconds", "progress_seconds"])
def test_supervisor_stops_resource_or_progress_failures(parity, executable, tmp_path, budget):
    from replay_contract import workload

    limits = dict(parity.DEFAULT_LIMITS, **{budget: 1 if budget == "rss_bytes" else 0.000001})
    trial = parity.run_trial(tmp_path / "limited", workload("fifo-smoke-v1"), executable, limits=limits)
    assert trial["status"] == "failed"
    assert "failure" in trial
    assert not (tmp_path / "limited/worker-result.json").exists()


def test_reference_build_identity_mismatch_never_contributes_success(parity, executable, tmp_path):
    from replay_contract import workload

    copied = tmp_path / "copied"
    copied.mkdir()
    shutil.copy(executable, copied / executable.name)
    metadata = json.loads(executable.with_name("build.json").read_text())
    metadata["executable_sha256"] = "wrong"
    (copied / "build.json").write_text(json.dumps(metadata))
    trial = parity.run_trial(tmp_path / "failed", workload("fifo-smoke-v1"), copied / executable.name)
    assert trial["status"] == "failed"
    assert "identity changed" in (tmp_path / "failed/stderr.txt").read_text()


def test_seeded_fee_mutation_in_reference_is_detected(parity, tmp_path, monkeypatch):
    from test_fifo_backtest import make_events

    source = tmp_path / "mutated-reference.cpp"
    original = parity.SOURCE.read_text()
    changed = original.replace("Wide(notional) * fee_rate + 999999", "Wide(notional) * fee_rate")
    assert changed != original
    source.write_text(changed)
    monkeypatch.setattr(parity, "SOURCE", source)
    executable = parity.build_reference(tmp_path / "mutated-build")
    events = make_events(5)
    events["book"]["bid_size"] = 0
    events["trade_price"][3] = 99
    events["trade_size"][3] = 3
    events["aggressor"][3] = -1
    config = dict(instruments=1, cash=1000, target_lots=3, rebalance_events=2,
                  latency_ns=0, fee_ppm=10000)
    native = TopOfBookBacktester(**config, execution_model="fifo")
    native.process_queue_batch(events)
    wrong = reference_result(parity, executable, tmp_path, events, config, 2)
    with pytest.raises(AssertionError):
        parity.compare_snapshot(native.result(), wrong)
