"""Byte parity, bounded ownership, and manifest safety for experimental readers."""

import ctypes
import hashlib
import importlib
import json
import os
import shutil
import threading
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "benchmarks"))
    return tuple(importlib.import_module(name) for name in ("replay_input", "controlled_replay", "replay_contract"))


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    if os.environ.get("GAMBIT_INPUT_TEST_LIBRARY"):
        return Path(os.environ["GAMBIT_INPUT_TEST_LIBRARY"])
    if not shutil.which("c++"):
        pytest.skip("C++ compiler required")
    import sys
    sys.path.insert(0, str(Path(__file__).parents[1] / "benchmarks"))
    module = importlib.import_module("replay_input")
    return module.build(tmp_path_factory.mktemp("input-build") / "library")


@pytest.mark.parametrize("seed", [0, 20260904, 2**64 - 1])
def test_fused_generation_matches_every_byte_at_boundaries(modules, library, seed):
    source, runner, _ = modules
    reader = source.InputChunks("fused", 65521, library=library)
    for offset, count in [(0, 1), (7, 19), (2047, 2051), (65520, 65521), (946943977, 23),
                          (((2**63 - 1) - 1_000_000) // 100_000_000, 1)]:
        events = reader.read(offset, count, seed)
        assert events.flags.aligned and not events.flags.writeable
        assert events.tobytes() == runner.make_queue_events(offset, count, seed=seed).tobytes()
        del events
    reader.close()


@pytest.mark.parametrize("mode", ["fused", "readinto", "mmap"])
@pytest.mark.parametrize("view_kind", ["field", "slice", "memoryview", "asarray"])
def test_retained_descendant_blocks_reuse_and_close(modules, library, tmp_path, mode, view_kind):
    source, runner, contract = modules
    spec = contract.workload("fifo-smoke-v1", 17, ticks=100)
    dataset = tmp_path / "dataset"
    runner.prepare_dataset(dataset, spec)
    reader = source.InputChunks(mode, 17, library=library if mode == "fused" else None,
                                dataset=None if mode == "fused" else dataset, size_bytes=8800)
    array = reader.read(0, 17)
    address = array.ctypes.data
    retained = {"field": lambda a: a["book"]["bid"], "slice": lambda a: a[2:8],
                "memoryview": memoryview, "asarray": np.asarray}[view_kind](array)
    del array
    with pytest.raises(BufferError, match="derived views"):
        reader.read(17, 17)
    with pytest.raises(BufferError):
        reader.close()
    del retained
    array = reader.read(17, 17)
    assert array.tobytes() == runner.make_queue_events(17, 17).tobytes()
    if mode != "mmap":
        assert array.ctypes.data == address
    del array
    reader.close()
    with pytest.raises(ValueError, match="closed"):
        reader.read(0, 1)


@pytest.mark.parametrize("mode", ["readinto", "mmap"])
def test_storage_extent_changes_are_rejected(modules, tmp_path, mode):
    source, runner, contract = modules
    spec = contract.workload("fifo-smoke-v1", 17, ticks=100)
    dataset = tmp_path / "dataset"
    runner.prepare_dataset(dataset, spec)
    reader = source.InputChunks(mode, 17, dataset=dataset, size_bytes=8800)
    with (dataset / "events.bin").open("r+b") as stream:
        stream.truncate(8000)
    with pytest.raises(ValueError, match="truncated"):
        reader.read(0, 17)
    with pytest.raises(ValueError, match="length changed"):
        reader.close()


def test_native_extent_checks_do_not_write_outside_buffer(modules, library):
    source, _, _ = modules
    reader = source.InputChunks("fused", 2, library=library)
    storage = ctypes.create_string_buffer(b"x" * 100)
    before = storage.raw
    assert reader._fill(ctypes.addressof(storage), 87, 0, 1, 0) == -1
    assert reader._fill(None, 88, 0, 1, 0) == -1
    assert reader._fill(ctypes.addressof(storage), 88, 2**64 - 1, 1, 0) == -1
    assert storage.raw == before
    with pytest.raises(ValueError, match="overflow"):
        reader.read(2**64, 1)
    reader.close()


@pytest.mark.native
def test_end_to_end_all_input_paths_match_and_remain_experimental(modules, library, tmp_path):
    _, runner, contract = modules
    spec = contract.workload("fifo-smoke-v1", 257, ticks=10003)
    dataset = tmp_path / "dataset"
    runner.prepare_dataset(dataset, spec)
    trials = []
    for mode in ("legacy", "fused", "readinto", "mmap"):
        result = runner.run_trial(tmp_path / mode, spec, session_id="input-test", input_mode=mode,
                                  input_library=library if mode == "fused" else None,
                                  dataset=dataset if mode in ("readinto", "mmap") else None)
        assert result["status"] == "ok", result
        trials.append(result)
    assert all(row["measurement"]["controls"] == trials[0]["measurement"]["controls"] for row in trials)
    assert "experimental input pipelines require separate qualification" in contract.summarize(trials)["ineligibility_reasons"]


@pytest.mark.native
@pytest.mark.parametrize("mode", ["readinto", "mmap"])
def test_storage_checksum_is_checked_after_reuse(modules, tmp_path, mode):
    _, runner, contract = modules
    spec = contract.workload("fifo-smoke-v1", 257, ticks=10003)
    dataset = tmp_path / "dataset"
    runner.prepare_dataset(dataset, spec)
    # An otherwise valid dataset with a wrong manifest digest must not be accepted.
    path = dataset / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["input_sha256"] = "0" * 64
    path.write_text(json.dumps(manifest))
    result = runner.run_trial(tmp_path / "trial", spec, session_id="corrupt", input_mode=mode, dataset=dataset)
    assert result["status"] == "failed"
    assert "checksum mismatch" in result["failure_reason"]


@pytest.mark.native
@pytest.mark.parametrize("mode", ["readinto", "mmap"])
def test_native_validation_error_is_not_masked_by_reader_cleanup(modules, tmp_path, mode):
    _, runner, contract = modules
    spec = contract.workload("fifo-smoke-v1", 17, ticks=100)
    dataset = tmp_path / "dataset"
    runner.prepare_dataset(dataset, spec)
    events = runner.make_queue_events(0, 100)
    events["aggressor"][0] = 3
    (dataset / "events.bin").write_bytes(events.tobytes())
    result = runner.run_trial(tmp_path / "trial", spec, session_id="invalid-record", input_mode=mode, dataset=dataset)
    assert result["status"] == "failed"
    assert "BufferError" not in result["failure_reason"]
    assert "invalid queue trade event" in result["failure_reason"]


def test_maximum_buffer_and_short_final_chunk_match_all_bytes(modules, library):
    source, runner, contract = modules
    reader = source.InputChunks("fused", contract.MAX_CHUNK, library=library)
    array = reader.read(0, contract.MAX_CHUNK)
    actual_hash = hashlib.sha256(memoryview(array).cast("B")).digest()
    del array
    array = reader.read(contract.MAX_CHUNK, 7)
    assert array.tobytes() == runner.make_queue_events(contract.MAX_CHUNK, 7).tobytes()
    assert array.nbytes == 7 * 88
    del array
    reader.close()
    del reader
    expected = runner.make_queue_events(0, contract.MAX_CHUNK)
    assert actual_hash == hashlib.sha256(memoryview(expected).cast("B")).digest()


def test_concurrent_refill_and_close_are_rejected(modules, library):
    source, _, _ = modules
    reader = source.InputChunks("fused", 17, library=library)
    entered, release = threading.Event(), threading.Event()
    original = reader._fill
    outputs, errors = [], []

    def paused(*args):
        entered.set()
        if not release.wait(2):
            raise TimeoutError("test producer was not released")
        return original(*args)

    def produce():
        try:
            outputs.append(reader.read(0, 17))
        except BaseException as error:
            errors.append(error)

    reader._fill = paused
    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    try:
        assert entered.wait(2)
        with pytest.raises(RuntimeError, match="concurrent"):
            reader.read(17, 17)
        with pytest.raises(RuntimeError, match="concurrent"):
            reader.close()
    finally:
        release.set()
        producer.join(2)
    assert not producer.is_alive() and not errors
    assert len(outputs) == 1
    outputs.clear()
    reader.close()


@pytest.mark.parametrize("mode", ["fused", "readinto", "mmap"])
def test_array_keeps_storage_alive_after_reader_is_discarded(modules, library, tmp_path, mode):
    source, runner, contract = modules
    dataset = tmp_path / "dataset"
    runner.prepare_dataset(dataset, contract.workload("fifo-smoke-v1", 17, ticks=100))
    reader = source.InputChunks(mode, 17, library=library if mode == "fused" else None,
                                dataset=None if mode == "fused" else dataset, size_bytes=8800)
    array = reader.read(17, 17)
    del reader
    assert array.tobytes() == runner.make_queue_events(17, 17).tobytes()
