"""Independent streaming full-trace FIFO comparison; never performance timing."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psutil
from controlled_replay import identity, observe_conditions, rss_bytes
from replay_contract import EXPECTED, digest, read_json, validate_workload, workload, write_json
from top_of_book_backtest import make_queue_events

from gambit.tick_backtest import QUEUE_DTYPE, TopOfBookBacktester

SCHEMA = "gambit-independent-fifo-parity-v1"
ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).with_name("fifo_reference.cpp")
CONFIG_KEYS = ("instruments", "cash", "target_lots", "rebalance_events", "fee_ppm", "latency_ns",
               "audit_capacity", "maximum_feed_age_ns")
SCALARS = ("processed", "cash", "equity", "net_pnl", "total_fees")
# Independent wire schema: all words are 64 bits, unlike Gambit's packed ID/status.
WIRE = {
    "orders": np.dtype([(n, "<u8" if n in ("id", "sequence", "instrument_id", "status") else "<i8")
                         for n in ("id", "sequence", "timestamp_ns", "quantity", "remaining", "instrument_id", "status")]),
    "fills": np.dtype([(n, "<u8" if n in ("order_id", "sequence", "instrument_id", "reserved") else "<i8")
                        for n in ("order_id", "sequence", "timestamp_ns", "quantity", "price", "fee", "instrument_id", "reserved")]),
    "queues": np.dtype([(n, "<u8" if n == "arrival_sequence" else "<i8")
                         for n in ("limit_price", "ahead", "initial_ahead", "arrival_sequence", "arrival_time_ns")]),
}
DEFAULT_LIMITS = dict(cpu_seconds=86400., wall_seconds=86400., progress_seconds=120., rss_bytes=4 * 1024**3)


def build_reference(output, *, sanitize=False):
    """Compile a separately identified executable; no pybind/Gambit dependencies."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    compiler = os.environ.get("CXX", "c++")
    executable = output / "fifo-reference"
    command = [compiler, "-std=c++11", "-O2", "-g", "-Wall", "-Wextra", "-Werror",
               *(["-fsanitize=address,undefined", "-fno-omit-frame-pointer"] if sanitize else []),
               str(SOURCE), "-o", str(executable)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=120)
    (output / "compiler.log").write_text(built.stdout + built.stderr)
    built.check_returncode()
    version = subprocess.run([compiler, "--version"], capture_output=True, text=True, check=True, timeout=10).stdout
    write_json(output / "build.json", dict(schema=SCHEMA, command=command, compiler_version=version,
                                           source_sha256=digest(SOURCE), executable_sha256=digest(executable),
                                           sanitized=sanitize))
    return executable


class Reference:
    def __init__(self, executable, config, stderr):
        if config.get("execution_model", "fifo") != "fifo":
            raise ValueError("reference supports only the approved FIFO policy")
        self.config = config
        self.child = subprocess.Popen([str(executable), *(str(config[k]) for k in CONFIG_KEYS)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr)

    def send(self, events):
        if events.dtype != QUEUE_DTYPE or events.ndim != 1 or not events.flags.c_contiguous:
            raise ValueError("reference requires contiguous QUEUE_DTYPE input")
        if not 1 <= len(events) <= 1048576:
            raise ValueError("reference input chunk outside bound")
        self.child.stdin.write(struct.pack("<Q", len(events)))
        self.child.stdin.write(memoryview(events).cast("B"))
        self.child.stdin.flush()

    def read(self, count):
        data = self.child.stdout.read(count)
        if len(data) != count:
            raise RuntimeError("reference terminated or returned a truncated snapshot; see reference-stderr.txt")
        return data

    def snapshot(self):
        self.child.stdin.write(struct.pack("<Q", 0))
        self.child.stdin.flush()
        if self.read(8) != b"GFIFO001":
            raise ValueError("invalid reference protocol version")
        values = struct.unpack("<QqqqqQQQQ", self.read(72))
        instruments, orders, fills, queues = values[5:]
        if (instruments != self.config["instruments"] or queues != orders or
                max(orders, fills) > self.config["audit_capacity"]):
            raise ValueError("reference snapshot exceeds declared bounds")
        result = dict(zip(SCALARS, values[:5]))
        result["positions"] = np.frombuffer(self.read(instruments * 8), dtype="<i8")
        for name, count in (("orders", orders), ("fills", fills), ("queues", queues)):
            result[name] = np.frombuffer(self.read(count * WIRE[name].itemsize), dtype=WIRE[name])
        return result

    def close(self):
        try:
            self.child.stdin.write(struct.pack("<Q", 2**64 - 1))
            self.child.stdin.close()
            if self.child.wait(timeout=5):
                raise RuntimeError("reference failed on shutdown")
            if self.child.stdout.read(1):
                raise ValueError("unexpected trailing reference output")
        finally:
            self.abort()

    def abort(self):
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait(timeout=5)
        for stream in (self.child.stdin, self.child.stdout):
            if not stream.closed:
                try:
                    stream.close()
                except BrokenPipeError:
                    pass


def compare_snapshot(actual, expected):
    """Compare values directly; digests are provenance, never the comparison."""
    for key in SCALARS:
        for result in (actual, expected):
            value = np.asarray(result[key])
            if value.shape or value.dtype.kind not in "iu":
                raise AssertionError(f"{key}: integer scalar required")
        if actual[key] != expected[key]:
            raise AssertionError(f"{key}: native={actual[key]}, reference={expected[key]}")
    if actual["positions"].dtype != np.dtype("<i8") or expected["positions"].dtype != np.dtype("<i8"):
        raise AssertionError("positions: signed 64-bit integers required")
    if not np.array_equal(actual["positions"], expected["positions"]):
        raise AssertionError("positions differ")
    for name, dtype in WIRE.items():
        if actual[name].dtype.names != dtype.names or expected[name].dtype != dtype:
            raise AssertionError(f"{name}: schema mismatch")
        if actual[name].shape != expected[name].shape:
            raise AssertionError(f"{name}: row count mismatch")
        for field in dtype.names:
            width = 4 if field in ("instrument_id", "status", "reserved") else 8
            native_dtype = np.dtype(f"<{dtype[field].kind}{width}")
            if actual[name].dtype[field] != native_dtype:
                raise AssertionError(f"{name}.{field}: native integer schema mismatch")
            different = actual[name][field] != expected[name][field]
            if np.any(different):
                row = int(np.argmax(different))
                raise AssertionError(f"{name}[{row}].{field}: native={actual[name][field][row]}, "
                                     f"reference={expected[name][field][row]}")


def controls(result, input_sha256):
    hashed = hashlib.sha256()
    for name in ("positions", "orders", "fills", "queues"):
        hashed.update(memoryview(result[name]).cast("B"))
    hashed.update(json.dumps({n: result[n] for n in SCALARS}, sort_keys=True).encode())
    return dict(input_sha256=input_sha256, result_sha256=hashed.hexdigest(),
                order_count=len(result["orders"]), fill_count=len(result["fills"]))


def run_worker(request, output):
    spec = request["workload"]
    validate_workload(spec)
    executable = Path(request["reference_executable"])
    build = read_json(executable.with_name("build.json"))
    if build["source_sha256"] != digest(SOURCE) or build["executable_sha256"] != digest(executable):
        raise ValueError("reference source/build identity changed")
    start = time.monotonic()
    observed = identity()
    native = TopOfBookBacktester(**spec["configuration"])
    hasher = hashlib.sha256()
    checkpoints = 0
    next_checkpoint = 1048576
    with (output / "reference-stderr.txt").open("xb") as errors, (output / "checkpoints.jsonl").open("x") as journal:
        reference = Reference(executable, spec["configuration"], errors)
        write_json(output / "reference-process.tmp", dict(pid=reference.child.pid))
        (output / "reference-process.tmp").rename(output / "reference-process.json")
        try:
            for offset in range(0, spec["ticks"], spec["chunk_size"]):
                events = make_queue_events(offset, min(spec["chunk_size"], spec["ticks"] - offset), seed=spec["seed"])
                events.setflags(write=False)
                hasher.update(memoryview(events).cast("B"))
                # Oracle receives a separate byte stream and owns separate state.
                reference.send(events)
                if native.process_queue_batch(events) != len(events):
                    raise AssertionError("native did not consume the complete chunk")
                processed = offset + len(events)
                del events
                if processed >= next_checkpoint or processed == spec["ticks"]:
                    expected = reference.snapshot()
                    actual = native.result()
                    compare_snapshot(actual, expected)
                    checkpoints += 1
                    journal.write(json.dumps(dict(processed=processed, orders=len(actual["orders"]),
                                                  fills=len(actual["fills"]), queues=len(actual["queues"]),
                                                  cash=actual["cash"], equity=actual["equity"],
                                                  total_fees=actual["total_fees"], positions=actual["positions"].tolist(),
                                                  elapsed_seconds=time.monotonic() - start, exact_match=True)) + "\n")
                    journal.flush()
                    next_checkpoint = processed + 1048576
                    if processed < spec["ticks"]:
                        del actual, expected
            reference.close()
        finally:
            reference.abort()
    checked = controls(actual, hasher.hexdigest())
    for key, value in EXPECTED.get(spec["scenario"], {}).items():
        if checked[key] != value:
            raise AssertionError(f"canonical {key} mismatch")
    traces = {}
    for label, result in (("native", actual), ("reference", expected)):
        path = output / f"{label}-trace.npz"
        with path.open("xb") as stream:
            np.savez_compressed(stream, **result)
        traces[label] = dict(path=path.name, sha256=digest(path), bytes=path.stat().st_size)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    reference_peak = child_usage.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    cpu = usage.ru_utime + usage.ru_stime + child_usage.ru_utime + child_usage.ru_stime
    peak_upper = rss_bytes() + reference_peak
    if cpu > request["limits"]["cpu_seconds"] or peak_upper > request["limits"]["rss_bytes"]:
        raise RuntimeError("final resource accounting exceeds oracle budget")
    report = dict(schema=SCHEMA, status="exact_trace_match", performance_qualification=False,
                  workload=spec, identity=observed, reference_build=build, driver_sha256=digest(Path(__file__)),
                  python_oracle_sha256=digest(ROOT / "tests/test_fifo_backtest.py"),
                  controls=checked, canonical_controls_checked=bool(EXPECTED.get(spec["scenario"])),
                  traces=traces,
                  comparison=dict(method="direct equality of every field of every audit row and portfolio checkpoint",
                                  checkpoints=checkpoints, processed=actual["processed"], orders=len(actual["orders"]),
                                  fills=len(actual["fills"]), queues=len(actual["queues"]),
                                  scalar_fields=list(SCALARS), audit_fields={k: list(v.names) for k, v in WIRE.items()},
                                  shared_portfolio_preserved=True, state_reset_at_chunk_boundaries=False),
                  portfolio={k: actual[k] for k in SCALARS}, positions=actual["positions"].tolist(),
                  order_status_counts={str(n): int(np.count_nonzero(actual["orders"]["status"] == n)) for n in range(5)},
                  resources=dict(elapsed_seconds=time.monotonic() - start, total_cpu_seconds=cpu,
                                 worker_peak_rss_bytes=rss_bytes(), reference_peak_rss_bytes=reference_peak,
                                 peak_rss_upper_bound_bytes=peak_upper, logical_input_bytes=spec["ticks"] * 88),
                  checkpoints_sha256=digest(output / "checkpoints.jsonl"))
    write_json(output / "worker-result.json", report)


def stop_group(child):
    # Workers and their reference subprocesses share an isolated process group.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait(timeout=2)


def run_trial(output, spec, executable, *, limits=None):
    validate_workload(spec)
    limits = DEFAULT_LIMITS if limits is None else limits
    if set(limits) != set(DEFAULT_LIMITS) or any(not 0 < limits[k] <= DEFAULT_LIMITS[k] for k in limits):
        raise ValueError("limits must be positive and no greater than the approved oracle budget")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    request = dict(workload=spec, reference_executable=str(Path(executable).resolve()), limits=limits)
    write_json(output / "request.json", request)
    start = time.monotonic()
    report = dict(schema=SCHEMA, status="failed", workload=spec, limits=limits,
                  conditions_before=observe_conditions(), performance_qualification=False)
    peak = 0
    with (output / "stdout.txt").open("xb") as stdout, (output / "stderr.txt").open("xb") as stderr:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_worker", str(output)],
                                 stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            worker = psutil.Process(child.pid)
            while child.poll() is None:
                now = time.monotonic()
                processes = [worker]
                pid_file = output / "reference-process.json"
                if pid_file.exists():
                    try:
                        processes.append(psutil.Process(read_json(pid_file)["pid"]))
                    except psutil.NoSuchProcess:
                        pass
                memory, cpu = 0, 0.
                for process in processes:
                    try:
                        memory += process.memory_info().rss
                        usage = process.cpu_times()
                        cpu += usage.user + usage.system
                    except psutil.NoSuchProcess:
                        pass
                peak = max(peak, memory)
                journal = output / "checkpoints.jsonl"
                progress_age = time.time() - journal.stat().st_mtime if journal.exists() else now - start
                if now - start > limits["wall_seconds"] or cpu > limits["cpu_seconds"]:
                    raise TimeoutError("oracle CPU/wall budget exhausted")
                if memory > limits["rss_bytes"]:
                    raise MemoryError("oracle process group exceeds memory budget")
                if progress_age > limits["progress_seconds"]:
                    raise TimeoutError("oracle comparison made no checkpoint progress")
                time.sleep(.05)
            if child.returncode:
                raise RuntimeError(f"parity worker exited {child.returncode}; retain stderr and partial checkpoints")
            result = read_json(output / "worker-result.json")
            if result["status"] != "exact_trace_match" or result["comparison"]["processed"] != spec["ticks"]:
                raise ValueError("incomplete parity result")
            report.update(status="exact_trace_match", worker_result_sha256=digest(output / "worker-result.json"),
                          comparison=result["comparison"], controls=result["controls"])
        except (Exception, KeyboardInterrupt) as error:
            stop_group(child)
            report["failure"] = f"{type(error).__name__}: {error}"
        finally:
            report.update(elapsed_seconds=time.monotonic() - start, observed_peak_group_rss_bytes=peak,
                          conditions_after=observe_conditions())
            write_json(output / "trial.json", report)
    return report


def run_campaign(output, executable):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    specs = [workload("fifo-3y-sparse-v1", chunk) for chunk in (65521, 65536, 1048576)]
    specs += [workload(name) for name in ("fifo-2y-sparse-v1", "fifo-1m-dense-v1", "fifo-2m-dense-v1")]
    summary = dict(schema=SCHEMA, status="incomplete", planned=specs, trials=[], total_cpu_seconds=0.,
                   limits=DEFAULT_LIMITS, performance_qualification=False)
    write_json(output / "campaign.json", summary)
    started = time.monotonic()
    try:
        for index, spec in enumerate(specs):
            limits = dict(DEFAULT_LIMITS,
                          cpu_seconds=DEFAULT_LIMITS["cpu_seconds"] - summary["total_cpu_seconds"],
                          wall_seconds=DEFAULT_LIMITS["wall_seconds"] - (time.monotonic() - started))
            directory = output / f"{index + 1:02d}-{spec['scenario']}-{spec['chunk_size']}"
            trial = run_trial(directory, spec, executable, limits=limits)
            summary["trials"].append(dict(directory=directory.name, status=trial["status"]))
            print(json.dumps(dict(directory=directory.name, status=trial["status"])), flush=True)
            if trial["status"] != "exact_trace_match":
                break
            result = read_json(directory / "worker-result.json")
            summary["total_cpu_seconds"] += result["resources"]["total_cpu_seconds"]
        if len(summary["trials"]) == len(specs) and all(t["status"] == "exact_trace_match" for t in summary["trials"]):
            primary = [read_json(output / t["directory"] / "worker-result.json") for t in summary["trials"][:3]]
            if len({json.dumps(t["controls"], sort_keys=True) for t in primary}) != 1:
                raise AssertionError("primary chunk controls differ")
            summary.update(status="exact_trace_match", primary_chunk_controls_equal=True)
    finally:
        summary["elapsed_seconds"] = time.monotonic() - started
        write_json(output / "summary.json", summary)
    return summary


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "_worker":
        output = Path(sys.argv[2])
        run_worker(read_json(output / "request.json"), output)
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    build = commands.add_parser("build")
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--sanitize", action="store_true")
    campaign = commands.add_parser("campaign")
    campaign.add_argument("--reference", type=Path, required=True)
    campaign.add_argument("--output-dir", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--reference", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--scenario", default="fifo-3y-sparse-v1")
    run.add_argument("--chunk-size", type=int, default=65521)
    args = parser.parse_args()
    if args.action == "build":
        print(build_reference(args.output_dir, sanitize=args.sanitize))
        return 0
    if args.action == "campaign":
        result = run_campaign(args.output_dir, args.reference)
        return 0 if result["status"] == "exact_trace_match" else 1
    result = run_trial(args.output_dir, workload(args.scenario, args.chunk_size), args.reference)
    print(json.dumps(result))
    return 0 if result["status"] == "exact_trace_match" else 1


if __name__ == "__main__":
    raise SystemExit(main())
