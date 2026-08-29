from __future__ import annotations

import pytest

from gambit.factor_device import _linux_sectors_written, inspect_factor_cache_device


def test_linux_diskstats_parser_reads_sectors_written() -> None:
    assert _linux_sectors_written("1 2 3 4 5 6 7 8 9 10 11") == 7
    with pytest.raises(ValueError, match="too few"):
        _linux_sectors_written("1 2")
    with pytest.raises(ValueError, match="negative"):
        _linux_sectors_written("1 2 3 4 5 6 -1")


def test_device_telemetry_never_claims_wear_without_smart_data(tmp_path) -> None:
    telemetry = inspect_factor_cache_device(tmp_path)

    assert telemetry.device_wear_measured is False
    assert telemetry.percentage_used is None
    if telemetry.available:
        assert telemetry.device_bytes_written is not None
        assert telemetry.source is not None
    else:
        assert telemetry.reason
