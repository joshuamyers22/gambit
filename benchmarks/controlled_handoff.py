"""Bounded, paired engineering measurements of the public concurrent factor path.

Run workers through separate Python environments to compare native builds. Arrival
probes are batch-completion observations, not per-record native timestamps.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import platform
import queue
import resource
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from gambit.factor_cache import TickFactorProcessor, TickRing

try:
    from .batch_metrics import HandoffMetrics
    from .tick_ring_benchmark import make_ticks
except ImportError:
    from batch_metrics import HandoffMetrics
    from tick_ring_benchmark import make_ticks


def percentiles(values):
    return dict(zip(("p50", "p95", "p99", "max"), map(float, np.percentile(values, [50, 95, 99, 100]))))


def measure(records, *, path="in_place", batch=1024, capacity=65536, rate=0,
            stall_seconds=0.0, probes=False, timeout=30.0, diagnostics=False,
            cleanup_timeout=0.25, cancel=None):
    """Time a complete drain with bounded workers and exact direct-factor parity.

    Scheduled offers never move when admission blocks. No offered records are
    skipped: admission waits for capacity and all records must reach the factors.
    The separate ring tests exercise reject-newest rather than retry semantics.
    """
    count = len(records)
    if (count < 1 or type(batch) is not int or type(capacity) is not int or
            batch < 1 or batch > capacity or capacity < 2 or capacity & (capacity - 1) or
            capacity > 1_048_576 or not all(math.isfinite(v) for v in
                                          (rate, timeout, stall_seconds, cleanup_timeout)) or
            rate < 0 or not 0 < timeout <= 300 or stall_seconds < 0 or not 0 <= cleanup_timeout <= 2):
        raise ValueError("invalid workload bounds")
    if path not in ("in_place", "copy", "lease", "direct"):
        raise ValueError("unknown consumer path")
    reference = TickFactorProcessor()
    reference.process_batch(records)
    expected = reference.snapshot
    processor = TickFactorProcessor()
    ring = TickRing(capacity)
    start = threading.Event()
    stop = threading.Event()
    errors = queue.Queue(maxsize=2)
    telemetry = HandoffMetrics(capacity, batch) if diagnostics else None
    completed_records = 0
    completions = []
    enqueued = np.zeros((count + batch - 1) // batch, dtype=np.int64) if probes else None
    high_water = 0
    admission_waits = 0
    epoch = 0
    deadline = 0

    def check():
        if cancel is not None and cancel.is_set():
            raise InterruptedError("handoff cancelled")
        if stop.is_set() or time.perf_counter_ns() > deadline:
            raise TimeoutError("handoff worker stopped or exceeded its deadline")

    def produce():
        nonlocal high_water, admission_waits
        for offset in range(0, count, batch):
            check()
            scheduled = None
            if rate:
                scheduled = epoch + int(offset / rate * 1e9)
                while (remaining := scheduled - time.perf_counter_ns()) > 0:
                    check()
                    stop.wait(min(remaining / 1e9, 0.001))
            end = min(offset + batch, count)
            waiting = False
            while True:
                depth = ring.depth
                if telemetry:
                    telemetry.sampled_high_water = max(telemetry.sampled_high_water, depth)
                    telemetry.full_observations += int(depth == capacity)
                if ring.capacity - depth >= end - offset:
                    break
                admission_waits += 1
                if telemetry:
                    telemetry.full_wait_polls += 1
                    telemetry.full_wait_episodes += int(not waiting)
                waiting = True
                check()
                time.sleep(0)
            check()
            if telemetry:
                offered = time.perf_counter_ns()
                telemetry.reserve(end - offset, offered, scheduled)
            if probes:
                enqueued[offset // batch] = time.perf_counter_ns()
            if ring.push_batch(records[offset:end]) != end - offset:
                raise RuntimeError("unexpected partial acceptance")
            if telemetry:
                telemetry.producer.add(time.perf_counter_ns() - offered, end - offset)
                telemetry.sampled_high_water = max(telemetry.sampled_high_water, ring.depth)
            if probes:
                high_water = max(high_water, ring.depth)
        ring.close()

    def consume():
        nonlocal completed_records
        consumed = 0
        stalled = False
        while consumed < count:
            check()
            if stall_seconds and not stalled and consumed >= count // 4:
                stop.wait(min(stall_seconds, max(0, (deadline - time.perf_counter_ns()) / 1e9)))
                check()
                stalled = True
            if telemetry:
                before_batch = time.perf_counter_ns()
            if path == "in_place":
                received = ring.wait_process_batch(processor, batch, spin_count=256, timeout_seconds=0.01)
            elif path == "copy":
                values = ring.wait_pop_batch(batch, spin_count=256, timeout_seconds=0.01)
                received = len(values)
                processor.process_batch(values)
            else:
                lease = ring.wait_lease_batch(batch, spin_count=256, timeout_seconds=0.01)
                values = None
                try:
                    values = lease.values
                    received = len(values)
                    processor.process_batch(values)
                finally:
                    del values
                    lease.close()
            completed_records += received
            if telemetry:
                stamp = time.perf_counter_ns()
                telemetry.consumer.add(stamp - before_batch, received)
                if received:
                    telemetry.complete(received, stamp)
            if received and probes:
                completions.append((consumed, consumed + received, time.perf_counter_ns()))
            consumed += received

    def guarded(function):
        start.wait()
        try:
            function()
        except BaseException as exc:
            # Failed Python consumer frames can retain a lease view through their
            # arguments. Release unwound frame locals before retaining the error;
            # preserve the exception type and traceback's stack/line information.
            traceback.clear_frames(exc.__traceback__)
            errors.put_nowait((function.__name__, exc))
            stop.set()
            ring.close()

    threads = [] if path == "direct" else [
        threading.Thread(target=guarded, args=(f,), daemon=True) for f in (produce, consume)
    ]
    for thread in threads:
        thread.start()
    before = resource.getrusage(resource.RUSAGE_SELF)
    cpu = time.process_time_ns()
    epoch = time.perf_counter_ns()
    deadline = epoch + int(timeout * 1e9)
    start.set()
    failure = None
    failure_stage = None
    shutdown_started = None
    try:
        if path == "direct":
            for offset in range(0, count, batch):
                check()
                completed_records += processor.process_batch(records[offset:offset + batch])
        else:
            for thread in threads:
                thread.join(max(0, (deadline - time.perf_counter_ns()) / 1e9))
            if not errors.empty():
                failure_stage, failure = errors.get_nowait()
            elif any(thread.is_alive() for thread in threads):
                failure_stage, failure = "join", TimeoutError("handoff threads did not finish")
    except BaseException as exc:
        failure_stage, failure = "caller", exc
    finally:
        shutdown_started = time.perf_counter_ns()
        stop.set()
        ring.close()
        cleanup_deadline = shutdown_started + int(cleanup_timeout * 1e9)
        for thread in threads:
            thread.join(max(0, (cleanup_deadline - time.perf_counter_ns()) / 1e9))
    live = [thread.name for thread in threads if thread.is_alive()]
    shutdown = dict(status="incomplete" if live else "joined", live_threads=live,
                    cleanup_seconds=(time.perf_counter_ns() - shutdown_started) / 1e9,
                    cleanup_limit_seconds=cleanup_timeout, run_limit_seconds=timeout,
                    process_isolation_required=bool(live))
    def failure_report(error, stage):
        metrics = ring.metrics if not live and path != "direct" else None
        error.handoff_report = dict(
            status="failed", failure=dict(stage=stage, type=type(error).__name__,
                                          message=str(error)[:512]), shutdown=shutdown,
            input_records=count, completed_records=completed_records if not live else None,
            accounting_final=not live, metrics=metrics,
            unaccepted_records=count - metrics["pushed"] if metrics else None,
            accepted_uncompleted_records=metrics["pushed"] - completed_records if metrics else None,
            diagnostics=telemetry.snapshot() if telemetry and not live else None,
            snapshot=None)
        return error

    if failure is not None:
        raise failure_report(failure, failure_stage)
    wall_ns = time.perf_counter_ns() - epoch
    cpu_ns = time.process_time_ns() - cpu
    after = resource.getrusage(resource.RUSAGE_SELF)
    try:
        snapshot = processor.snapshot
        if snapshot != expected or snapshot["processed"] != count or snapshot["sequence_errors"]:
            raise AssertionError("complete factor snapshot differs from direct processing")
        metrics = ring.metrics
        if path != "direct" and (metrics["pushed"] != count or metrics["popped"] != count
                                 or metrics["dropped"] or metrics["depth"]):
            raise AssertionError("handoff lost, rejected, or retained records")
    except BaseException as exc:
        raise failure_report(exc, "validation")
    result = dict(path=path, count=count, batch=batch, capacity=capacity, rate=rate,
                  stall_seconds=stall_seconds, probes=probes, wall_ns=wall_ns, cpu_ns=cpu_ns,
                  peak_rss_bytes=after.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                  minor_faults=after.ru_minflt - before.ru_minflt,
                  major_faults=after.ru_majflt - before.ru_majflt,
                  voluntary_switches=after.ru_nvcsw - before.ru_nvcsw,
                  involuntary_switches=after.ru_nivcsw - before.ru_nivcsw,
                  sampled_high_water=high_water if probes else None,
                  admission_waits=admission_waits, metrics=metrics, snapshot=snapshot,
                  status="ok", shutdown=shutdown, failure=None,
                  diagnostics=telemetry.snapshot() if telemetry else None)
    if probes and path != "direct":
        finished = np.empty(count, dtype=np.int64)
        for begin, end, stamp in completions:
            finished[begin:end] = stamp
        batch_indices = np.arange(count) // batch
        result["enqueue_call_to_completion_ns"] = percentiles(finished - enqueued[batch_indices])
        if rate:
            scheduled = epoch + (batch_indices * batch / rate * 1e9).astype(np.int64)
            result["scheduled_to_completion_ns"] = percentiles(finished - scheduled)
            result["admission_lateness_ns"] = percentiles(enqueued[batch_indices] - scheduled)
            result["drain_after_last_offer_ns"] = int(finished[-1] - scheduled[-1])
    return result


def worker(scale=1.0):
    import gambit._factor_cache as native

    records = make_ticks(max(8192, int(8_000_000 * scale)))
    # All builds use the same warmup and the same full snapshot comparison.
    measure(records[:8192])
    scenarios = [
        ("saturated", len(records), {}),
        ("saturated_probes", len(records), dict(probes=True)),
        ("direct", len(records), dict(path="direct")),
        ("copy", min(len(records), 262144), dict(path="copy")),
        ("lease", min(len(records), 262144), dict(path="lease")),
        ("producer_limited", min(len(records), 262144), dict(rate=1_000_000, probes=True)),
        ("burst_2x", min(len(records), 262144), dict(rate=2_000_000, probes=True)),
        ("consumer_stall", min(len(records), 262144),
         dict(rate=2_000_000, capacity=2048, stall_seconds=0.01, probes=True)),
    ]
    return dict(schema=1, python=sys.version, platform=platform.platform(), machine=platform.machine(),
                native_path=native.__file__, native_sha256=hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
                input_sha256=hashlib.sha256(records.tobytes()).hexdigest(),
                cases={name: measure(records[:count], **options) for name, count, options in scenarios})


def paired_summary(rows, baseline, candidate):
    rng = np.random.default_rng(606)
    result = {}
    for case in rows[0][baseline]["cases"]:
        result[case] = {}
        for field in ("wall_ns", "cpu_ns", "peak_rss_bytes"):
            a = np.array([row[baseline]["cases"][case][field] for row in rows], dtype=float)
            b = np.array([row[candidate]["cases"][case][field] for row in rows], dtype=float)
            reduction = 1 - b / a
            bootstrap = np.median(rng.choice(reduction, (10000, len(rows))), axis=1)
            result[case][field] = dict(baseline=percentiles(a), candidate=percentiles(b),
                                      median_paired_reduction=float(np.median(reduction)),
                                      bootstrap_95_ci=list(map(float, np.percentile(bootstrap, [2.5, 97.5]))))
    return result


def campaign(variants, output, trials=30, scale=1.0):
    if trials < 1 or len(variants) < 2 or len(variants) != len(set(variants)):
        raise ValueError("at least two distinct builds and one trial required")
    output.mkdir(parents=True, exist_ok=False)
    orders = list(itertools.permutations(variants))
    rows = []
    script = str(Path(__file__).resolve())
    for trial in range(trials):
        row = {}
        order = orders[trial % len(orders)]
        for name in order:
            completed = subprocess.run([variants[name], script, "worker", "--scale", str(scale)],
                                       capture_output=True, text=True, timeout=300, check=True,
                                       env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"})
            row[name] = json.loads(completed.stdout)
        if len({item["input_sha256"] for item in row.values()}) != 1:
            raise AssertionError("builds processed different inputs")
        for case in next(iter(row.values()))["cases"]:
            snapshots = [item["cases"][case]["snapshot"] for item in row.values()]
            if any(snapshot != snapshots[0] for snapshot in snapshots):
                raise AssertionError("cross-build factor mismatch")
        rows.append(row)
        (output / f"trial-{trial:02d}.json").write_text(json.dumps(dict(order=order, results=row), indent=2) + "\n")
        print(f"completed trial {trial + 1}/{trials}", flush=True)
    names = list(variants)
    summary = dict(trials=trials, scale=scale, variants=variants,
                   harness_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   comparisons={name: paired_summary(rows, names[0], name) for name in names[1:]})
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    single = sub.add_parser("worker")
    single.add_argument("--scale", type=float, default=1.0)
    compare = sub.add_parser("campaign")
    compare.add_argument("--variant", action="append", required=True, help="name=/absolute/python-or-wrapper")
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--trials", type=int, default=30)
    compare.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    if args.scale <= 0 or args.scale > 2:
        parser.error("scale must be in (0, 2]")
    if args.command == "worker":
        print(json.dumps(worker(args.scale)))
    else:
        campaign(dict(value.split("=", 1) for value in args.variant), args.output, args.trials, args.scale)


if __name__ == "__main__":
    main()
