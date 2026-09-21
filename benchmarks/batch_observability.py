"""Paired on/off batch-diagnostics screen; every worker has an outer deadline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import numpy as np
from controlled_handoff import measure
from tick_ring_benchmark import make_ticks


def worker(enabled, ticks):
    import gambit._factor_cache as native

    records = make_ticks(ticks)
    measure(records[:8192])
    cases = {"saturated": (ticks, {}),
             "copy": (min(ticks, 262144), dict(path="copy")),
             "lease": (min(ticks, 262144), dict(path="lease")),
             "scheduled": (min(ticks, 262144), dict(rate=1_000_000)),
             "burst_2x": (min(ticks, 262144), dict(rate=2_000_000)),
             "stall": (min(ticks, 262144), dict(rate=2_000_000, capacity=2048, stall_seconds=.01))}
    return dict(status="ok", diagnostics=enabled, native_sha256=sha(Path(native.__file__)),
                input_sha256=hashlib.sha256(memoryview(records).cast("B")).hexdigest(),
                python=sys.version, platform=platform.platform(),
                cases={name: measure(records[:count], diagnostics=enabled, **options)
                       for name, (count, options) in cases.items()})


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(values):
    values = sorted(values)
    return {name: float(values[max(0, int(np.ceil(len(values) * fraction)) - 1)])
            for name, fraction in (("p50", .5), ("p95", .95), ("p99", .99), ("max", 1))}


def summarize(rows):
    if not rows:
        raise ValueError("no trials")
    identities = set()
    workloads = set()
    for row in rows:
        for mode in ("off", "on"):
            value = row[mode]
            if value["status"] != "ok" or value["diagnostics"] != (mode == "on"):
                raise ValueError("failed or mislabeled worker")
            identities.add(tuple(value[k] for k in ("native_sha256", "input_sha256", "python", "platform")))
        if row["off"]["cases"].keys() != row["on"]["cases"].keys():
            raise ValueError("case mismatch")
        for case, off in row["off"]["cases"].items():
            on = row["on"]["cases"][case]
            if (off["snapshot"] != on["snapshot"] or any(off[k] != on[k] for k in
                    ("path", "count", "batch", "capacity", "rate", "stall_seconds"))):
                raise ValueError("workload or factor mismatch")
            for item in (off, on):
                if item["status"] != "ok" or item["shutdown"]["status"] != "joined":
                    raise ValueError("incomplete handoff")
        workloads.add(json.dumps({case: {k: item[k] for k in
                                         ("path", "count", "batch", "capacity", "rate", "stall_seconds")}
                                  for case, item in row["off"]["cases"].items()}, sort_keys=True))
    if len(identities) != 1:
        raise ValueError("input/build/host mismatch")
    if len(workloads) != 1:
        raise ValueError("workload changed across pairs")
    rng = np.random.default_rng(20260920)
    result = {}
    for case in rows[0]["off"]["cases"]:
        result[case] = {}
        for metric in ("wall_ns", "cpu_ns", "peak_rss_bytes"):
            off = np.array([row["off"]["cases"][case][metric] for row in rows], dtype=float)
            on = np.array([row["on"]["cases"][case][metric] for row in rows], dtype=float)
            if not np.all(np.isfinite(off)) or not np.all(np.isfinite(on)) or np.any(off <= 0) or np.any(on <= 0):
                raise ValueError("invalid measurements")
            overhead = on / off - 1
            interval = list(map(float, np.percentile(np.median(
                rng.choice(overhead, (10000, len(rows))), axis=1), [2.5, 97.5])))
            result[case][metric] = dict(off=distribution(off), on=distribution(on),
                                       median_relative_overhead=float(np.median(overhead)),
                                       bootstrap_95_interval=interval,
                                       one_percent_target="insufficient_pairs" if len(rows) < 30 else
                                       "within" if interval[1] <= .01 else
                                       "exceeds" if interval[0] > .01 else "inconclusive")
    return dict(pairs=len(rows), diagnostic_only=True, performance_qualification=False,
                bootstrap_seed=20260920, bootstrap_resamples=10000, cases=result)


def campaign(output, pairs, ticks):
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for pair in range(pairs):
        row = {}
        for mode in (("off", "on") if pair % 2 == 0 else ("on", "off")):
            command = [sys.executable, str(Path(__file__).resolve()), "worker", "--mode", mode,
                       "--ticks", str(ticks)]
            try:
                process = subprocess.run(command, capture_output=True, text=True, timeout=120,
                                         env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
                (output / f"{pair:02d}-{mode}-stderr.txt").write_text(process.stderr)
                row[mode] = json.loads(process.stdout)
                if process.returncode != 0 and row[mode].get("status") == "ok":
                    row[mode] = dict(status="failed", failure="nonzero worker exit", returncode=process.returncode)
            except subprocess.TimeoutExpired:
                row[mode] = dict(status="failed", failure="outer process timeout", process_killed=True)
            except (ValueError, OSError) as error:
                row[mode] = dict(status="failed", failure=str(error)[:512])
            (output / f"{pair:02d}-{mode}.json").write_text(json.dumps(row[mode], indent=2) + "\n")
            if row[mode]["status"] != "ok":
                raise RuntimeError("failed worker retained; campaign cannot qualify")
        rows.append(row)
        print(f"completed pair {pair + 1}/{pairs}", flush=True)
    summary = summarize(rows)
    summary["sources"] = {name: sha(Path(__file__).with_name(name)) for name in
                          ("batch_observability.py", "batch_metrics.py", "controlled_handoff.py", "tick_ring_benchmark.py")}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("worker", "campaign"))
    parser.add_argument("--mode", choices=("off", "on"), default="off")
    parser.add_argument("--ticks", type=int, default=8_000_000)
    parser.add_argument("--pairs", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 8192 <= args.ticks <= 8_000_000 or not 1 <= args.pairs <= 200:
        parser.error("ticks must be 8192..8000000; pairs must be 1..200")
    if args.command == "worker":
        try:
            print(json.dumps(worker(args.mode == "on", args.ticks)))
        except BaseException as error:
            print(json.dumps(getattr(error, "handoff_report", dict(status="failed", failure=str(error)[:512]))))
            return 1
    else:
        if args.output is None:
            parser.error("campaign requires --output")
        campaign(args.output, args.pairs, args.ticks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
