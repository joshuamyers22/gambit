"""Conservative host-device I/O telemetry for cache benchmark deltas."""

from __future__ import annotations

import os
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class FactorCacheDeviceTelemetry:
    root: str
    measured_at_ns: int
    device_id: int
    platform: str
    available: bool
    source: str | None
    reason: str | None
    sectors_written: int | None
    device_bytes_written: int | None
    sector_bytes: int | None
    device_wear_measured: bool = False
    percentage_used: float | None = None

    def snapshot(self) -> dict[str, object]:
        return asdict(self)


def _linux_sectors_written(stat_text: str) -> int:
    fields = stat_text.split()
    if len(fields) < 7:
        raise ValueError("Linux block-device statistics have too few fields")
    sectors = int(fields[6])
    if sectors < 0:
        raise ValueError("Linux block-device sectors written cannot be negative")
    return sectors


def inspect_factor_cache_device(
    root: str | Path,
    *,
    sysfs_root: str | Path = "/sys",
) -> FactorCacheDeviceTelemetry:
    """Read cumulative Linux block writes; never infer SSD wear from host writes."""
    store = Path(root)
    if not store.is_dir() or store.is_symlink():
        raise ValueError("factor cache root must be an existing non-symlink directory")
    resolved = str(store.resolve())
    measured_at_ns = time.time_ns()
    device_id = store.stat().st_dev
    system = platform.system()
    unavailable = {
        "root": resolved,
        "measured_at_ns": measured_at_ns,
        "device_id": device_id,
        "platform": system,
        "available": False,
        "source": None,
        "sectors_written": None,
        "device_bytes_written": None,
        "sector_bytes": None,
    }
    if system != "Linux":
        return FactorCacheDeviceTelemetry(**unavailable, reason="Linux sysfs block statistics are unavailable")
    device_link = Path(sysfs_root) / "dev" / "block" / f"{os.major(device_id)}:{os.minor(device_id)}"
    try:
        device_path = device_link.resolve(strict=True)
        stat_path = device_path / "stat"
        sectors = _linux_sectors_written(stat_path.read_text())
    except (OSError, ValueError) as error:
        return FactorCacheDeviceTelemetry(**unavailable, reason=f"block statistics unavailable: {error}")
    sector_bytes = 512  # Linux diskstats defines sectors in 512-byte units.
    return FactorCacheDeviceTelemetry(
        root=resolved,
        measured_at_ns=measured_at_ns,
        device_id=device_id,
        platform=system,
        available=True,
        source=str(stat_path),
        reason=None,
        sectors_written=sectors,
        device_bytes_written=sectors * sector_bytes,
        sector_bytes=sector_bytes,
    )


__all__ = ["FactorCacheDeviceTelemetry", "inspect_factor_cache_device"]
