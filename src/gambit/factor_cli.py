"""Safe JSON command-line operations for the experimental factor cache."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence

from gambit.factor_operations import calibrate_factor_cache, inspect_factor_cache
from gambit.factor_store import FactorStoreError, collect_garbage, evict_factor_nodes

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
    return parser


def _write_result(value: object, output: Path | None) -> None:
    encoded = json.dumps(value, sort_keys=True, indent=2) + "\n"
    if output is None:
        print(encoded, end="")
    else:
        output.write_text(encoded)


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    try:
        if parsed.command == "inventory":
            result = inspect_factor_cache(parsed.root).snapshot()
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
        else:  # pragma: no cover - argparse enforces the command set
            raise AssertionError(f"unsupported command: {parsed.command}")
    except (FactorStoreError, OSError, RuntimeError, ValueError) as error:
        _write_result(
            {"error": type(error).__name__, "message": str(error), "ok": False},
            parsed.output,
        )
        return 1
    _write_result(result, parsed.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
