"""Acceptance exercises for the persisted-research lifecycle contract."""

from __future__ import annotations

import errno
import multiprocessing
import os
import shutil
from pathlib import Path

import polars as pl
import pytest

from gambit.backtest_result import BacktestBundleError, BacktestResult, BacktestTelemetry
from gambit.configuration import RunConfiguration, RunProvenance

ROOT = Path(__file__).parents[1]
LIFECYCLE_PATH = ROOT / "documentation" / "source" / "data_lifecycle.rst"
pytestmark = [pytest.mark.acceptance, pytest.mark.integration]


def _result() -> BacktestResult:
    frame = pl.DataFrame({"value": [1.0]})
    telemetry = BacktestTelemetry(
        stages=(),
        timestamps_processed=1,
        orders_proposed=0,
        orders_accepted=0,
        orders_rejected=0,
        orders_filled=0,
        orders_cancelled=0,
        orders_open=0,
        trades_executed=0,
    )
    return BacktestResult(
        provenance=RunProvenance(
            configuration=RunConfiguration(),
            input_fingerprints={"fixture": "sha256:data-lifecycle"},
            package_version="test",
            git_commit="0" * 40,
        ),
        telemetry=telemetry,
        trades=frame,
        orders=frame,
        decisions=frame,
        pnl=frame,
        risk_measures=frame,
        risk_exposures=frame,
        risk_attribution=frame,
        stress_results=frame,
        validation_findings=frame,
    )


def _terminate_before_bundle_rename(root: str) -> None:
    import gambit.backtest_result as persistence

    original_fsync_directory = persistence._fsync_directory

    def fsync_then_terminate(path: Path) -> None:
        original_fsync_directory(path)
        if path.parent == Path(root) and path.name.startswith(".run.gambit."):
            os._exit(86)

    persistence._fsync_directory = fsync_then_terminate
    _result().save(Path(root) / "run.gambit")


def test_verified_backup_restores_after_primary_corruption(tmp_path: Path) -> None:
    primary = _result().save(tmp_path / "primary.gambit")
    backup = tmp_path / "backup.gambit"
    shutil.copytree(primary, backup)

    expected_fingerprint = BacktestResult.load(primary).provenance.run_fingerprint
    assert BacktestResult.load(backup).provenance.run_fingerprint == expected_fingerprint

    with (primary / "trades.arrow").open("ab") as member:
        member.write(b"corrupt")
    with pytest.raises(BacktestBundleError, match="checksum mismatch"):
        BacktestResult.load(primary)

    restored = tmp_path / "restored.gambit"
    shutil.copytree(backup, restored)
    recovered = BacktestResult.load(restored)
    assert recovered.provenance.run_fingerprint == expected_fingerprint
    assert recovered.trades.equals(_result().trades)


def test_process_death_cannot_publish_partial_bundle_and_rerun_recovers(tmp_path: Path) -> None:
    destination = tmp_path / "run.gambit"
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_terminate_before_bundle_rename, args=(str(tmp_path),))

    process.start()
    process.join(timeout=30)

    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        pytest.fail("bundle publication subprocess did not terminate")
    assert process.exitcode == 86
    assert not destination.exists()
    staging = list(tmp_path.glob(".run.gambit.*"))
    assert len(staging) == 1

    shutil.rmtree(staging[0])
    expected = _result()
    expected.save(destination)
    restored = BacktestResult.load(destination)
    assert restored.provenance.run_fingerprint == expected.provenance.run_fingerprint


@pytest.mark.parametrize(
    ("error_type", "error_number"),
    [(OSError, errno.ENOSPC), (PermissionError, errno.EACCES)],
    ids=["full-disk", "permission-denied"],
)
def test_result_storage_failures_publish_nothing_and_clean_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[OSError],
    error_number: int,
) -> None:
    import gambit.backtest_result as persistence

    destination = tmp_path / "failed.gambit"

    def fail_digest(_path: Path) -> str:
        raise error_type(error_number, os.strerror(error_number))

    monkeypatch.setattr(persistence, "_file_digest", fail_digest)
    with pytest.raises(error_type) as raised:
        _result().save(destination)

    assert raised.value.errno == error_number
    assert not destination.exists()
    assert list(tmp_path.glob(".failed.gambit.*")) == []


def test_lifecycle_contract_is_linked_and_keeps_external_ownership_explicit() -> None:
    policy = LIFECYCLE_PATH.read_text()
    assert "RPO: the last externally retained source input" in policy
    assert "RTO: the time the caller needs" in policy
    assert "Gambit makes no fixed duration claim" in policy
    assert "Do not back up a factor cache as authoritative data" in policy
    assert "Never overwrite or repair" in policy
    assert "ENOSPC" in policy and "EACCES" in policy
    for document in ("PROJECT_BRIEF.md", "API_STABILITY.md", "RELEASE_READINESS.md"):
        assert "data_lifecycle" in (ROOT / document).read_text()
