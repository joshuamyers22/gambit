"""Policy checks for the experimental native replay measurement contract."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
BUDGET = ROOT / "LATENCY_BUDGET.md"


def test_latency_budget_keeps_proposal_and_acceptance_separate() -> None:
    policy = BUDGET.read_text()
    prose = " ".join(policy.split())
    for heading in (
        "## Scope",
        "## End-to-end objective",
        "## Stage budget",
        "## Time and ordering",
        "## Resource bounds",
        "## Evidence",
        "## Change control",
    ):
        assert heading in policy
    assert "candidate; not approved for production acceptance" in prose
    assert "misses the proposed five-second objective" in prose
    assert "A missed target keeps the capability experimental" in prose
    assert "three historical runs do not establish p95" in prose
    assert "zero dropped/reordered records" in prose
    assert "full-volume independent trace parity absent" in prose
    assert "explicit promote, retarget, or remain-experimental decision" in prose


def test_native_replay_contract_and_reports_link_the_budget() -> None:
    documents = (
        "documentation/architecture/native_tick_backtest.md",
        "documentation/architecture/adr_native_tick_replay.md",
        "documentation/performance/fifo_backtest_2026-09-04.md",
        "documentation/performance/top_of_book_backtest_2026-09-04.md",
        "documentation/performance/crypto_tick_parity_2026-09-04.md",
    )
    for document in documents:
        assert "../../LATENCY_BUDGET.md" in (ROOT / document).read_text()


def test_latency_evidence_points_to_preserved_raw_results() -> None:
    policy = BUDGET.read_text()
    assert "fifo_backtest_2026-09-04.md" in policy
    assert "top_of_book_backtest_2026-09-04.md" in policy
    assert "crypto_tick_parity_2026-09-04.md" in policy
    for artifact in (
        "fifo_backtest_2026-09-04_3y_trial1.json",
        "fifo_backtest_2026-09-04_3y_trial2.json",
        "fifo_backtest_2026-09-04_3y_trial3.json",
        "top_of_book_backtest_2026-09-04_final_3y.json",
        "crypto_tick_parity_2026-09-04.json",
    ):
        assert (ROOT / "documentation" / "performance" / artifact).is_file()
