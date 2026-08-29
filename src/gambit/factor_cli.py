"""Safe JSON command-line operations for the experimental factor cache."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence

from gambit.factor_device import inspect_factor_cache_device
from gambit.factor_metrics import (
    FactorMetricsError,
    format_prometheus_metrics,
    read_factor_cache_metrics,
)
from gambit.factor_operations import (
    calibrate_factor_cache,
    inspect_factor_cache,
    inspect_factor_cache_health,
)
from gambit.factor_store import (
    FactorStoreError,
    collect_garbage,
    enforce_factor_cache_quota,
    evict_factor_nodes,
)

_SIZE_PATTERN = re.compile(r"([0-9]+)([kmgt]?i?b)?", re.IGNORECASE)
_SIZE_MULTIPLIERS = {
    None: 1,
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
}


def _parse_bytes(value: str) -> int:
    match = _SIZE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise argparse.ArgumentTypeError("size must be an integer with an optional KiB/MiB/GiB suffix")
    suffix = match.group(2).lower() if match.group(2) else None
    return int(match.group(1)) * _SIZE_MULTIPLIERS[suffix]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gambit-factor-cache")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory", help="inspect cache state without mutation")
    inventory.add_argument("root", type=Path)
    inventory.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")

    metrics = subparsers.add_parser("metrics", help="read persistent lifetime counters")
    metrics.add_argument("root", type=Path)
    metrics.add_argument("--prometheus", action="store_true", help="emit Prometheus text format")
    metrics.add_argument("--openmetrics", action="store_true", help="emit OpenMetrics text format")
    metrics.add_argument("--output", type=Path, help="write output to this file instead of stdout")

    health = subparsers.add_parser("health", help="evaluate cache health thresholds")
    health.add_argument("root", type=Path)
    health.add_argument("--minimum-free-bytes", type=_parse_bytes, default=0)
    health.add_argument("--max-cache-bytes", type=_parse_bytes)
    health.add_argument("--max-unindexed-generations", type=int, default=0)
    health.add_argument("--max-staging-generations", type=int, default=0)
    health.add_argument("--old-lease-seconds", type=float, default=86400.0)
    health.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")

    device = subparsers.add_parser("device", help="inspect conservative host-device write telemetry")
    device.add_argument("root", type=Path)
    device.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")

    calibrate = subparsers.add_parser("calibrate", help="measure native cache costs on a selected device")
    calibrate.add_argument("root", type=Path)
    calibrate.add_argument("--small-sample", type=_parse_bytes, default=1024 * 1024)
    calibrate.add_argument("--large-sample", type=_parse_bytes, default=16 * 1024 * 1024)
    calibrate.add_argument("--repeats", type=int, default=3)
    calibrate.add_argument("--no-page-cache-eviction", action="store_true")
    calibrate.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")

    collect = subparsers.add_parser("collect", help="plan or apply orphan collection")
    collect.add_argument("root", type=Path)
    collect.add_argument("--stale-lease-seconds", type=float, default=86400.0)
    collect.add_argument(
        "--metadata-retention-seconds",
        type=float,
        default=30 * 86400.0,
        help="retain orphan access/admission records for this long",
    )
    collect.add_argument("--apply", action="store_true", help="perform deletion; default is dry-run")
    collect.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")

    evict = subparsers.add_parser("evict", help="plan or apply bounded LRU eviction")
    evict.add_argument("root", type=Path)
    evict.add_argument("--max-bytes", type=_parse_bytes, required=True)
    evict.add_argument("--max-nodes", type=int)
    evict.add_argument("--apply", action="store_true", help="perform deletion; default is dry-run")
    evict.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")

    quota = subparsers.add_parser("quota", help="plan or enforce a whole-cache allocation budget")
    quota.add_argument("root", type=Path)
    quota.add_argument("--max-cache-bytes", type=_parse_bytes, required=True)
    quota.add_argument("--reserve-free-bytes", type=_parse_bytes, default=0)
    quota.add_argument("--high-watermark", type=float, default=0.9)
    quota.add_argument("--low-watermark", type=float, default=0.8)
    quota.add_argument("--apply", action="store_true", help="perform deletion; default is dry-run")
    quota.add_argument("--output", type=Path, help="write JSON to this file instead of stdout")
    return parser


def _write_result(value: object, output: Path | None) -> None:
    encoded = json.dumps(value, sort_keys=True, indent=2) + "\n"
    if output is None:
        print(encoded, end="")
    else:
        output.write_text(encoded)


def _write_text(value: str, output: Path | None) -> None:
    if output is None:
        print(value, end="")
    else:
        output.write_text(value)


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    health_ok = True
    try:
        if parsed.command == "inventory":
            result = inspect_factor_cache(parsed.root).snapshot()
        elif parsed.command == "device":
            result = inspect_factor_cache_device(parsed.root).snapshot()
        elif parsed.command == "metrics":
            metrics = read_factor_cache_metrics(parsed.root)
            if parsed.prometheus or parsed.openmetrics:
                _write_text(
                    format_prometheus_metrics(metrics, openmetrics=parsed.openmetrics),
                    parsed.output,
                )
                return 0
            result = metrics.snapshot()
        elif parsed.command == "health":
            health = inspect_factor_cache_health(
                parsed.root,
                minimum_free_bytes=parsed.minimum_free_bytes,
                max_cache_allocated_bytes=parsed.max_cache_bytes,
                max_unindexed_generations=parsed.max_unindexed_generations,
                max_staging_generations=parsed.max_staging_generations,
                old_lease_seconds=parsed.old_lease_seconds,
            )
            health_ok = health.ok
            result = health.snapshot()
        elif parsed.command == "calibrate":
            result = calibrate_factor_cache(
                parsed.root,
                sample_bytes=(parsed.small_sample, parsed.large_sample),
                repeats=parsed.repeats,
                request_page_cache_eviction=not parsed.no_page_cache_eviction,
            ).snapshot()
        elif parsed.command == "collect":
            result = collect_garbage(
                parsed.root,
                stale_lease_seconds=parsed.stale_lease_seconds,
                metadata_retention_seconds=parsed.metadata_retention_seconds,
                dry_run=not parsed.apply,
            )
        elif parsed.command == "evict":
            result = evict_factor_nodes(
                parsed.root,
                max_bytes=parsed.max_bytes,
                max_nodes=parsed.max_nodes,
                dry_run=not parsed.apply,
            )
        elif parsed.command == "quota":
            result = enforce_factor_cache_quota(
                parsed.root,
                max_cache_bytes=parsed.max_cache_bytes,
                reserve_free_bytes=parsed.reserve_free_bytes,
                high_watermark=parsed.high_watermark,
                low_watermark=parsed.low_watermark,
                dry_run=not parsed.apply,
            )
        else:  # pragma: no cover - argparse enforces the command set
            raise AssertionError(f"unsupported command: {parsed.command}")
    except (FactorMetricsError, FactorStoreError, OSError, RuntimeError, ValueError) as error:
        _write_result(
            {"error": type(error).__name__, "message": str(error), "ok": False},
            parsed.output,
        )
        return 1
    _write_result(result, parsed.output)
    return 0 if health_ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
