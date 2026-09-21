"""Paired full-harness input screening, preserving controlled replay supervision."""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import numpy as np
from controlled_replay import run_trial, validate_dataset
from replay_contract import distribution, read_json, workload, write_json
from replay_input import MODES


def compare(rows, candidate):
    """Keep each campaign separate; only the intended input implementation differs."""
    if not rows:
        raise ValueError("at least one pair is required")
    for row in rows:
        a, b = row["legacy"], row[candidate]
        if a["status"] != "ok" or b["status"] != "ok":
            raise ValueError("failed trials cannot form an accepted comparison")
        for field in ("workload", "diagnostic"):
            if a[field] != b[field]:
                raise ValueError("workload/diagnostic mismatch")
        if a["measurement"]["controls"] != b["measurement"]["controls"]:
            raise ValueError("input/result mismatch")
        for field in ("kind", "manifest_sha256", "cache_state"):
            if a["source"][field] != b["source"][field]:
                raise ValueError("source/cache mismatch")
        for field in ("host", "native_extension_sha256", "files", "runner_sha256", "contract_code_sha256",
                      "generator_sha256", "input_reader_sha256"):
            if a["identity"][field] != b["identity"][field]:
                raise ValueError("unintended build/runtime change")
    for mode in ("legacy", candidate):
        # Git status is retained as context; content/build hashes identify code.
        identities = [{k: v for k, v in row[mode]["identity"].items() if k != "dirty_state"} for row in rows]
        if any(value != identities[0] for value in identities):
            raise ValueError("implementation identity changed during screening")
    rng = np.random.default_rng(20260920)
    metrics = {}
    for field in ("harness_seconds", "execution_seconds", "peak_rss_bytes", "cpu_seconds"):
        a = np.array([row["legacy"]["measurement"][field] for row in rows])
        b = np.array([row[candidate]["measurement"][field] for row in rows])
        if np.any(a <= 0):
            raise ValueError("nonpositive baseline metric")
        changes = 1 - b / a
        indices = rng.integers(0, len(rows), size=(10000, len(rows)))
        interval = np.quantile(np.median(changes[indices], axis=1), [.025, .975]).tolist()
        baseline, treatment = distribution(a.tolist()), distribution(b.tolist())
        metrics[field] = dict(baseline=baseline, candidate=treatment,
                              median_paired_reduction=float(np.median(changes)), bootstrap_95_interval=interval,
                              p95_ratio=treatment["p95"] / baseline["p95"])
    primary = metrics["harness_seconds"]
    retain = (len(rows) >= 30 and primary["median_paired_reduction"] >= .1 and
              primary["bootstrap_95_interval"][0] > 0 and all(v["p95_ratio"] <= 1.05 for v in metrics.values()))
    return dict(pairs=len(rows), metrics=metrics, retention_signal=retain,
                interpretation="full-harness screening only; review correctness, CPU and tails separately",
                bootstrap_seed=20260920, bootstrap_resamples=10000, performance_qualification=False)


def campaign(output, spec, modes, *, library=None, dataset=None, pairs=30, prefix=None, warm_cache=False):
    if (not 1 <= pairs <= 30 or not 2 <= len(modes) <= 3 or modes[0] != "legacy" or
            len(set(modes)) != len(modes) or any(mode not in MODES for mode in modes)):
        raise ValueError("one baseline and distinct input variants, at most 30 pairs required")
    if warm_cache and not dataset:
        raise ValueError("cache preparation requires a dataset")
    if dataset:
        validate_dataset(dataset, spec)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    orders = list(itertools.permutations(modes))
    write_json(output / "campaign.json", dict(workload=spec, modes=modes, pairs=pairs, prefix=prefix,
                                               cache="sequential pre-read" if warm_cache else "uncontrolled",
                                               primary_metric="harness_seconds"))
    rows = []
    for index in range(pairs):
        row = {}
        for mode in orders[index % len(orders)]:
            if warm_cache:
                with (Path(dataset) / "events.bin").open("rb") as stream:
                    while stream.read(1024 * 1024):
                        pass
            trial = run_trial(output / f"{index + 1:03d}-{mode}", spec, session_id=output.name,
                              variant="baseline" if mode == "legacy" else "candidate", input_mode=mode,
                              input_library=library if mode == "fused" else None, dataset=dataset,
                              cache_state="operator-warm" if warm_cache else "uncontrolled",
                              cache_evidence="full sequential pre-read before worker launch; OS residency not attested" if warm_cache else None,
                              diagnostic=dict(prefix_ticks=prefix) if prefix is not None else None)
            row[mode] = trial
            print(json.dumps(dict(pair=index + 1, mode=mode, status=trial["status"], measurement=trial["measurement"])), flush=True)
            if trial["status"] != "ok":
                write_json(output / "failure.json", dict(pair=index + 1, mode=mode, trial=trial))
                raise RuntimeError(trial["failure_reason"])
        rows.append(row)
    summary = {mode: compare(rows, mode) for mode in modes[1:]}
    write_json(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario", default="fifo-3y-sparse-v1")
    parser.add_argument("--chunk-size", type=int, default=65521)
    parser.add_argument("--modes", nargs="+", default=["legacy", "fused"])
    parser.add_argument("--library", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--pairs", type=int, default=30)
    parser.add_argument("--prefix", type=int)
    parser.add_argument("--warm-cache", action="store_true")
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    if args.summarize:
        plan = read_json(args.output / "campaign.json")
        rows = [{mode: read_json(args.output / f"{i + 1:03d}-{mode}/trial.json") for mode in plan["modes"]}
                for i in range(plan["pairs"])]
        print(json.dumps({mode: compare(rows, mode) for mode in plan["modes"][1:]}, indent=2))
    else:
        campaign(args.output, workload(args.scenario, args.chunk_size), args.modes, library=args.library,
                 dataset=args.dataset, pairs=args.pairs, prefix=args.prefix, warm_cache=args.warm_cache)


if __name__ == "__main__":
    main()
