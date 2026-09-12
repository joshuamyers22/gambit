"""Acceptance contracts for historical financial-output disposition."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
LEDGER_PATH = ROOT / "historical_output_corrections.json"
REGISTER_PATH = ROOT / "HISTORICAL_OUTPUT_REGISTER.csv"
POLICY_PATH = ROOT / "HISTORICAL_OUTPUT_DISPOSITION.md"
REQUIRED_IDS = {
    "pending_position_cap",
    "heartbeat_execution_lag",
    "multiplier_aware_entry_sizing",
    "vwap_future_observation_causality",
    "vwap_invalid_terms",
    "callback_and_fill_integrity",
    "numeric_admission_and_overflow",
}
REGISTER_FIELDS = [
    "output_id",
    "location",
    "provenance_git_commit",
    "run_fingerprint",
    "package_version",
    "correction_ids",
    "trigger_status",
    "disposition",
    "replacement_run_fingerprint",
    "reviewer",
    "reviewed_at_utc",
    "notes",
]
pytestmark = pytest.mark.acceptance


def ledger() -> dict[str, Any]:
    return json.loads(LEDGER_PATH.read_text())


def test_correction_ledger_covers_required_financial_change_families() -> None:
    data = ledger()
    assert data["schema_version"] == 1
    assert data["policy"] == POLICY_PATH.name
    assert data["register"] == REGISTER_PATH.name
    assert data["allowed_dispositions"] == ["retain", "rerun_required", "invalidate"]
    assert data["allowed_trigger_statuses"] == ["affected", "unaffected", "unknown"]
    assert data["missing_or_unverifiable_provenance"] == (
        "rerun_required_if_sources_exist_else_invalidate"
    )
    assert data["inventory"] == {
        "tracked_result_bundles": 0,
        "external_inventory_required": True,
        "owner": "quant/domain owner",
    }
    assert {correction["id"] for correction in data["corrections"]} == REQUIRED_IDS


def test_every_correction_has_auditable_commits_disposition_and_evidence() -> None:
    data = ledger()
    commits: list[str] = []
    for correction in data["corrections"]:
        assert correction["category"]
        assert correction["trigger"]
        assert correction["reason"]
        assert correction["affected_disposition"] in {"rerun_required", "invalidate"}
        assert correction["correction_commits"]
        for commit in correction["correction_commits"]:
            assert re.fullmatch(r"[0-9a-f]{40}", commit), correction["id"]
            commits.append(commit)
        assert correction["evidence"]
        for evidence in correction["evidence"]:
            assert evidence.startswith("tests/test_")
            assert (ROOT / evidence).is_file(), (correction["id"], evidence)
    assert len(commits) == len(set(commits))


def test_policy_names_every_rule_and_conservative_unknown_default() -> None:
    policy = " ".join(POLICY_PATH.read_text().split())
    for correction_id in REQUIRED_IDS:
        assert f"`{correction_id}`" in policy
    assert "Package version `1.1.0` alone is not a cutoff" in policy
    assert "If a faithful rerun is impossible, invalidate" in policy
    assert "An empty external register is not evidence" in policy


def test_historical_output_register_is_an_empty_owner_template() -> None:
    with REGISTER_PATH.open(newline="") as handle:
        register = csv.DictReader(handle)
        assert register.fieldnames == REGISTER_FIELDS
        assert list(register) == []


@pytest.mark.parametrize(
    "document",
    ["API_STABILITY.md", "PROJECT_BRIEF.md", "RELEASE_READINESS.md"],
)
def test_product_contracts_link_historical_output_policy(document: str) -> None:
    assert POLICY_PATH.name in (ROOT / document).read_text()
