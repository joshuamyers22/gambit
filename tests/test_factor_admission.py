from __future__ import annotations

import json

from gambit.factor_admission import clear_rejection, has_recent_rejection, record_rejection

NODE_KEY = "a" * 64
POLICY_KEY = "b" * 64


def test_factor_admission_rejection_round_trip_and_clear(tmp_path) -> None:
    record_rejection(
        tmp_path,
        NODE_KEY,
        POLICY_KEY,
        compute_seconds=0.125,
        output_bytes=4096,
    )

    assert has_recent_rejection(tmp_path, NODE_KEY, POLICY_KEY, ttl_seconds=60)
    assert not has_recent_rejection(tmp_path, NODE_KEY, "c" * 64, ttl_seconds=60)

    clear_rejection(tmp_path, NODE_KEY)
    assert not has_recent_rejection(tmp_path, NODE_KEY, POLICY_KEY, ttl_seconds=60)


def test_factor_admission_rejection_expires_without_mutating_state(tmp_path) -> None:
    record_rejection(
        tmp_path,
        NODE_KEY,
        POLICY_KEY,
        compute_seconds=0.125,
        output_bytes=4096,
    )

    assert not has_recent_rejection(tmp_path, NODE_KEY, POLICY_KEY, ttl_seconds=0)
    assert (tmp_path / "admission" / f"{NODE_KEY}.json").is_file()


def test_factor_admission_ignores_corrupt_or_substituted_hints(tmp_path) -> None:
    directory = tmp_path / "admission"
    directory.mkdir()
    hint = directory / f"{NODE_KEY}.json"
    hint.write_text(json.dumps({"decision": "decline"}))
    assert not has_recent_rejection(tmp_path, NODE_KEY, POLICY_KEY, ttl_seconds=60)

    hint.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    hint.symlink_to(outside)
    assert not has_recent_rejection(tmp_path, NODE_KEY, POLICY_KEY, ttl_seconds=60)
