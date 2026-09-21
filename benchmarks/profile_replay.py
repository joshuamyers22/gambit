"""LAT-03 diagnostic profiles and paired probe overhead; never qualification."""

from __future__ import annotations

import argparse
import hashlib
import json
import pstats
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import psutil
from controlled_replay import identity, observe_conditions, run_trial
from replay_contract import digest, distribution, workload, write_json

SCHEMA = "gambit-replay-diagnosis-v1"


def overhead_summary(trials):
    modes = {mode: [t for t in trials if t["diagnostic"]["instrumentation"] == mode]
             for mode in ("timers_only", "full")}
    low, high = modes["timers_only"], modes["full"]
    if not low or len(low) != len(high) or any(t["status"] != "ok" for t in trials):
        raise ValueError("overhead experiment requires complete successful pairs")
    if any(a["workload"] != b["workload"] or a["source"] != b["source"] or a["identity"] != b["identity"] or
           a["diagnostic"].get("prefix_ticks") != b["diagnostic"].get("prefix_ticks") or
           a["measurement"]["controls"] != b["measurement"]["controls"] for a, b in zip(low, high)):
        raise ValueError("paired identity, input, prefix or result mismatch")
    if len({json.dumps(t["identity"], sort_keys=True) for t in trials}) != 1:
        raise ValueError("identity changed during overhead campaign")
    result = {}
    for metric in ("execution_seconds", "harness_seconds"):
        control = np.array([t["measurement"][metric] for t in low])
        instrumented = np.array([t["measurement"][metric] for t in high])
        if np.any(control <= 0) or not np.all(np.isfinite(control)) or not np.all(np.isfinite(instrumented)):
            raise ValueError("invalid diagnostic timings")
        effects = instrumented / control - 1
        indices = np.random.default_rng(20260920).integers(0, len(low), size=(10000, len(low)))
        lower, upper = np.quantile(np.median(effects[indices], axis=1), [.025, .975]).tolist()
        conclusion = ("within_1_percent" if upper <= .01 else
                      "exceeds_1_percent" if lower > .01 else "inconclusive_at_1_percent")
        if len(low) < 30:
            conclusion = "insufficient_pairs"
        result[metric] = dict(timers_only=distribution(control.tolist()), full=distribution(instrumented.tolist()),
                              median_relative_overhead=float(np.median(effects)),
                              paired_bootstrap_95_interval=[lower, upper], conclusion=conclusion,
                              pair_relative_overheads=effects.tolist())
    return dict(schema=SCHEMA, diagnostic_only=True, pairs=len(low), input_result_parity=True,
                metrics=result, seed=20260920, bootstrap_resamples=10000,
                limitations=["compulsory native-call clocks retained in both modes",
                             "parent RSS polling and outer watchdogs retained",
                             "single development session, correlated noise possible",
                             "prefix results do not establish full-volume overhead or production tails"])


def clock_calibration(iterations=1_000_000, repeats=9):
    """Unsubtracted loop-cost observations bound the residual compulsory probe cost."""
    values = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        for _ in range(iterations):
            time.monotonic_ns()
        values.append((time.perf_counter_ns() - started) / iterations)
    return dict(iterations=iterations, repeats=repeats, ns_per_call_including_loop=distribution(values),
                interpretation="loop included; not a clock accuracy or hard worst-case guarantee")


def overhead(args):
    if not 1 <= args.pairs <= 30:
        raise ValueError("overhead screening is bounded to 1–30 pairs")
    spec = workload(args.scenario, args.chunk_size)
    if not 1 <= args.prefix_ticks <= spec["ticks"]:
        raise ValueError("prefix must not exceed scenario")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "experiment.json", dict(schema=SCHEMA, diagnostic_only=True, purpose="paired instrumentation overhead",
                                                scenario=spec, prefix_ticks=args.prefix_ticks, planned_pairs=args.pairs,
                                                runner_sha256=digest(Path(__file__)), conditions_before=observe_conditions(),
                                                clock_calibration=clock_calibration()))
    trials = []
    for pair in range(args.pairs):
        modes = ("timers_only", "full") if pair % 2 == 0 else ("full", "timers_only")
        for mode in modes:
            trial = run_trial(output / f"{pair + 1:03d}-{mode}", spec, session_id="lat03-overhead",
                              diagnostic=dict(instrumentation=mode, prefix_ticks=args.prefix_ticks))
            trials.append(trial)
            print(json.dumps(dict(pair=pair + 1, mode=mode, status=trial["status"],
                                  measurement=trial["measurement"])), flush=True)
            if trial["status"] != "ok":
                write_json(output / "failure.json", dict(schema=SCHEMA, failed_trial=trial["trial_id"],
                                                          reason=trial["failure_reason"], completed_trials=len(trials)))
                return 1
    summary = overhead_summary(trials)
    summary["conditions_after"] = observe_conditions()
    write_json(output / "summary.json", summary)
    return 0


def python_profile(args):
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    spec = workload(args.scenario, args.chunk_size)
    trial = run_trial(output / "trial", spec, session_id="lat03-cprofile",
                      diagnostic=dict(instrumentation="full", prefix_ticks=args.prefix_ticks, cprofile=True))
    if trial["status"] != "ok":
        return 1
    stats = pstats.Stats(str(output / "trial/python-profile.pstats"))
    rows = [dict(file=key[0], line=key[1], function=key[2], primitive_calls=value[0], calls=value[1],
                 self_seconds=value[2], cumulative_seconds=value[3]) for key, value in stats.stats.items()]
    rows.sort(key=lambda row: row["self_seconds"], reverse=True)
    write_json(output / "python-profile.json", dict(schema=SCHEMA, diagnostic_only=True,
                                                     total_seconds=stats.total_tt, functions=rows,
                                                     limitation="Python deterministic profiler perturbs timings; native calls are opaque"))
    return 0


def ring_worker(output, seconds):
    from tick_ring_benchmark import benchmark_native_in_place, make_ticks

    output = Path(output)
    record = dict(schema=SCHEMA, diagnostic_only=True, identity=identity(),
                  workload=dict(records_per_iteration=1_000_000, batch_size=1024, capacity=65536,
                                spin_count=256, park_timeout_seconds=.01, instruments=1),
                  repetitions=[])
    records = make_ticks(1_000_000)
    records.setflags(write=False)
    record["input_sha256"] = hashlib.sha256(memoryview(records).cast("B")).hexdigest()
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        result = benchmark_native_in_place(records, 1024, 65536)
        if result.sequence_errors or result.rejected_pushes:
            raise AssertionError("ring profile lost/reordered records")
        record["repetitions"].append(asdict(result))
    record["elapsed_seconds"] = time.monotonic() - start
    write_json(output / "ring-result.json", record)


def native_sample(args):
    if sys.platform != "darwin":
        raise ValueError("this sampling driver requires macOS sample; use perf on a separately identified Linux host")
    if not 1 <= args.seconds <= 30:
        raise ValueError("sample duration must be 1–30 seconds")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # The FIFO's native code runs in a separately supervised worker; sample that
    # child, not the Python supervisor waiting on its protocol pipe.
    mode = "_fifo" if args.path == "fifo" else "_ring"
    child_command = [sys.executable, str(Path(__file__).resolve()), mode, str(output), str(args.seconds + 3)]
    sample_record = dict(schema=SCHEMA, diagnostic_only=True, path=args.path, child_command=child_command,
                         conditions_before=observe_conditions(), profiler="/usr/bin/sample",
                         runner_sha256=digest(Path(__file__)))
    with (output / "worker-stdout.txt").open("xb") as stdout, (output / "worker-stderr.txt").open("xb") as stderr:
        child = subprocess.Popen(child_command, stdout=stdout, stderr=stderr)
        try:
            target = child.pid if args.path == "ring" else None
            deadline = time.monotonic() + 10
            while target is None and time.monotonic() < deadline and child.poll() is None:
                for descendant in psutil.Process(child.pid).children(recursive=True):
                    if "_worker" in descendant.cmdline():
                        target = descendant.pid
                        break
                if target is None:
                    time.sleep(.05)
            if target is None:
                raise RuntimeError("could not identify the native replay worker")
            sample_command = ["/usr/bin/sample", str(target), str(args.seconds), "1", "-mayDie", "-fullPaths",
                              "-file", str(output / "sample.txt")]
            sample_record["sample_command"] = sample_command
            sampled = subprocess.run(sample_command, capture_output=True, text=True, timeout=args.seconds + 30)
            sample_record.update(sample_returncode=sampled.returncode, sample_stdout=sampled.stdout,
                                 sample_stderr=sampled.stderr, sampled_pid=target)
            sample_record["worker_returncode"] = child.wait(timeout=150)
        except BaseException as error:
            sample_record["error"] = f"{type(error).__name__}: {error}"
            # The FIFO supervisor owns its isolated child and enforces its bounds;
            # let it reap the worker rather than orphaning a native process.
            try:
                child.wait(timeout=150)
            except subprocess.TimeoutExpired:
                for descendant in psutil.Process(child.pid).children(recursive=True):
                    descendant.kill()
                child.kill()
                child.wait(timeout=5)
            raise
        finally:
            sample_record["conditions_after"] = observe_conditions()
            write_json(output / "sample-manifest.json", sample_record)
    return 0 if sample_record["sample_returncode"] == 0 and sample_record["worker_returncode"] == 0 else 1


def main():
    if len(sys.argv) == 4 and sys.argv[1] in ("_fifo", "_ring"):
        output = Path(sys.argv[2])
        if sys.argv[1] == "_ring":
            ring_worker(output, int(sys.argv[3]))
            return 0
        trial = run_trial(output / "trial", workload("fifo-3y-sparse-v1"), session_id="lat03-native-sample",
                          diagnostic=dict(instrumentation="full"))
        return 0 if trial["status"] == "ok" else 1
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("overhead", "python"):
        command_parser = sub.add_parser(name)
        command_parser.add_argument("--scenario", default="fifo-3y-sparse-v1")
        command_parser.add_argument("--prefix-ticks", type=int, default=100_000_000)
        command_parser.add_argument("--chunk-size", type=int, default=65521)
        command_parser.add_argument("--output-dir", type=Path, required=True)
        if name == "overhead":
            command_parser.add_argument("--pairs", type=int, default=30)
    sampler = sub.add_parser("sample")
    sampler.add_argument("--path", choices=("fifo", "ring"), required=True)
    sampler.add_argument("--seconds", type=int, default=20)
    sampler.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "sample":
            return native_sample(args)
        if args.action == "python":
            return python_profile(args)
        return overhead(args)
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
