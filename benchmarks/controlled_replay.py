"""Controlled FIFO measurements. See documentation/operations/controlled_replay.md."""

from __future__ import annotations

import argparse
import cProfile
import gc
import hashlib
import json
import os
import platform
import resource
import selectors
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psutil
from batch_metrics import Histogram
from replay_contract import (
    CONTRACT,
    EXPECTED,
    LIMITS,
    SCENARIOS,
    SCHEMA,
    digest,
    distribution,
    read_json,
    summarize,
    validate_workload,
    workload,
    write_json,
)
from replay_input import MODES, InputChunks
from top_of_book_backtest import make_queue_events, reconcile

from gambit.tick_backtest import QUEUE_DTYPE, TopOfBookBacktester

ROOT = Path(__file__).resolve().parents[1]
CANCELLED = False


def command(args, cwd=None):
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=3, check=False)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def observe_host():
    return dict(system=platform.system(), release=platform.release(), machine=platform.machine(),
                macos=platform.mac_ver()[0], logical_cpus=os.cpu_count(),
                memory_bytes=psutil.virtual_memory().total,
                cpu=command(["sysctl", "-n", "machdep.cpu.brand_string"]) if sys.platform == "darwin"
                else platform.processor(),
                os_build=command(["sw_vers", "-buildVersion"]) if sys.platform == "darwin" else None,
                python=platform.python_version(), numpy=np.__version__)


def observe_conditions():
    return dict(observed_at=datetime.now(timezone.utc).isoformat(), load_average=list(os.getloadavg()),
                power=command(["pmset", "-g", "batt"]) if sys.platform == "darwin" else None,
                thermal=command(["pmset", "-g", "therm"]) if sys.platform == "darwin" else None,
                power_settings=command(["pmset", "-g", "custom"]) if sys.platform == "darwin" else None)


def identity():
    import gambit

    native = Path(sys.modules[TopOfBookBacktester.__module__].__file__) if TopOfBookBacktester else None
    # Derive the source root from the imported checkout, including baseline variants.
    source_root = Path(gambit.__file__).resolve().parents[2]
    files = ["setup.py", "uv.lock", "src/gambit/tick_backtest.py",
             "src/gambit/cpp/factor_cache/top_of_book_backtest.cpp"]
    return dict(host=observe_host(), revision=command(["git", "rev-parse", "HEAD"], source_root),
                dirty_state=command(["git", "status", "--porcelain"], source_root),
                files={name: digest(source_root / name) if (source_root / name).is_file() else None for name in files},
                native_extension_sha256=digest(native) if native else None,
                runner_sha256=digest(Path(__file__)), contract_code_sha256=digest(Path(__file__).with_name("replay_contract.py")),
                generator_sha256=digest(Path(__file__).with_name("top_of_book_backtest.py")),
                input_reader_sha256=digest(Path(__file__).with_name("replay_input.py")),
                batch_metrics_sha256=digest(Path(__file__).with_name("batch_metrics.py")))


def reference_matches(value):
    host = value["host"]
    return all(host.get(key) == expected for key, expected in dict(
        system="Darwin", cpu="Apple M4", logical_cpus=10, memory_bytes=25769803776,
        machine="arm64", macos="15.5", os_build="24F74", python="3.10.20", numpy="2.2.6",
    ).items())


def build_attested(manifest, value):
    """Build provenance is an explicit record, never inferred from compiler installation."""
    if not manifest:
        return False
    required = ("compiler_version", "compile_commands", "link_commands", "native_extension_sha256", "source_sha256")
    if any(not manifest.get(key) for key in required):
        raise ValueError("incomplete build manifest")
    if (manifest["native_extension_sha256"] != value["native_extension_sha256"] or
            manifest["source_sha256"] != value["files"]["src/gambit/cpp/factor_cache/top_of_book_backtest.cpp"]):
        raise ValueError("build manifest does not identify the imported extension/source")
    commands = manifest["compile_commands"] + manifest["link_commands"]
    if not isinstance(commands, list) or any(not isinstance(c, list) or not all(isinstance(a, str) for a in c)
                                             for c in commands):
        raise ValueError("build commands must be lists of argument lists")
    flags = [a for c in commands for a in c]
    if any(a.startswith(("-ffast-math", "-Ofast", "-march=", "-mcpu=", "-fsanitize=")) for a in flags):
        raise ValueError("build flags are outside the v1 reference contract")
    return ("clang-1700.0.13.5" in manifest["compiler_version"] and "-std=c++11" in flags and "-O3" in flags)


def session_evidence(path):
    if path is None:
        return None
    value = read_json(path)
    for key in ("operator", "quiet_period_start_utc", "quiet_period_end_utc", "background_workloads",
                "power_thermal_notes", "independence_notes"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"session evidence requires {key}")
    start = datetime.fromisoformat(value["quiet_period_start_utc"])
    end = datetime.fromisoformat(value["quiet_period_end_utc"])
    if start.tzinfo is None or end.tzinfo is None or (end - start).total_seconds() < 300:
        raise ValueError("session evidence must identify a five-minute timezone-aware quiet interval")
    if not 0 <= (datetime.now(timezone.utc) - end).total_seconds() <= 300:
        raise ValueError("quiet interval must end within five minutes before this session")
    if value.get("ac_power") is not True or value.get("low_power_mode") is not False:
        raise ValueError("session evidence requires AC power and low-power mode off")
    return value


def prepare_dataset(output, spec):
    validate_workload(spec)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    hasher = hashlib.sha256()
    try:
        with (output / "events.bin").open("xb") as stream:
            for offset in range(0, spec["ticks"], spec["chunk_size"]):
                events = make_queue_events(offset, min(spec["chunk_size"], spec["ticks"] - offset), seed=spec["seed"])
                data = memoryview(events).cast("B")
                if stream.write(data) != len(data):
                    raise OSError("short dataset write")
                hasher.update(data)
        result = dict(schema=SCHEMA, contract=CONTRACT, workload=spec, record_dtype=QUEUE_DTYPE.descr,
                      byte_order="little", record_bytes=88, input_sha256=hasher.hexdigest(),
                      size_bytes=spec["ticks"] * 88, generator_sha256=digest(Path(__file__).with_name("top_of_book_backtest.py")))
        expected = EXPECTED.get(spec["scenario"], {}).get("input_sha256")
        if expected and result["input_sha256"] != expected:
            raise ValueError("generated corpus differs from canonical input")
        # Presence of manifest.json commits a complete dataset; incomplete bytes remain diagnostic evidence.
        write_json(output / "manifest.json", result)
        return result
    except BaseException as error:
        write_json(output / "failure.json", dict(error=type(error).__name__, message=str(error)))
        raise


def validate_dataset(directory, spec):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    validate_workload(manifest["workload"])
    # Chunking may differ; bytes, event semantics and full record count must not.
    left, right = dict(manifest["workload"]), dict(spec)
    left.pop("chunk_size")
    right.pop("chunk_size")
    if left != right or manifest["schema"] != SCHEMA or manifest["contract"] != CONTRACT:
        raise ValueError("dataset workload/schema mismatch")
    if (manifest["record_bytes"] != 88 or manifest["byte_order"] != "little" or
            manifest["record_dtype"] != json.loads(json.dumps(QUEUE_DTYPE.descr))):
        raise ValueError("dataset record layout mismatch")
    value = manifest["input_sha256"]
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("invalid dataset digest")
    if manifest["size_bytes"] != spec["ticks"] * 88 or (directory / "events.bin").stat().st_size != manifest["size_bytes"]:
        raise ValueError("dataset length mismatch")
    return manifest


def emit(phase, **extra):
    print(json.dumps(dict(phase=phase, at_ns=time.monotonic_ns(), **extra), allow_nan=False), flush=True)


def rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def checkpoint():
    if CANCELLED:
        raise InterruptedError("benchmark cancelled")
    if rss_bytes() > LIMITS["rss"]:
        raise MemoryError("replay process exceeds 512 MiB")


def diagnostic_options(spec, options):
    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - {"instrumentation", "prefix_ticks", "cprofile", "batch_metrics"}:
        raise ValueError("unknown diagnostic options")
    if options.get("instrumentation", "full") not in ("full", "timers_only"):
        raise ValueError("unknown instrumentation mode")
    count = options.get("prefix_ticks", spec["ticks"])
    if type(count) is not int or not 1 <= count <= spec["ticks"]:
        raise ValueError("diagnostic prefix must be within the declared workload")
    if "cprofile" in options and type(options["cprofile"]) is not bool:
        raise ValueError("cprofile option must be boolean")
    if "batch_metrics" in options and type(options["batch_metrics"]) is not bool:
        raise ValueError("batch_metrics option must be boolean")
    return dict(options)


def run_worker(request, output):
    spec = request["workload"]
    validate_workload(spec)
    diagnostic = diagnostic_options(spec, request.get("diagnostic"))
    total_ticks = diagnostic.get("prefix_ticks", spec["ticks"])
    probes = diagnostic.get("instrumentation", "full") == "full"
    # The compulsory native-call timer remains in both modes. Optional stage
    # clocks and per-batch telemetry/RSS are disabled only in diagnostic runs.
    stage_clock = time.monotonic_ns if probes else lambda: 0
    if TopOfBookBacktester is None:
        raise RuntimeError("native extension required")
    emit("setup")
    observed = identity()
    input_mode = request.get("input_mode", "legacy")
    if input_mode not in MODES:
        raise ValueError("unknown input mode")
    if input_mode == "fused":
        observed["input_library_sha256"] = digest(Path(request["input_library"]))
        observed["input_source_sha256"] = digest(Path(__file__).with_name("fused_queue_input.cpp"))
    attested = build_attested(request.get("build_manifest"), observed)
    emit("identity", identity=observed, build_attested=attested)
    warm_start = time.monotonic_ns()
    warm = TopOfBookBacktester(**spec["configuration"])
    for offset in range(0, 1_000_000, spec["chunk_size"]):
        checkpoint()
        data = make_queue_events(offset, min(spec["chunk_size"], 1_000_000 - offset), seed=spec["seed"])
        data.setflags(write=False)
        warm.process_queue_batch(data)
    del warm, data
    gc.collect()
    warm_seconds = (time.monotonic_ns() - warm_start) / 1e9
    checkpoint()
    profiler = cProfile.Profile() if diagnostic.get("cprofile") else None
    if profiler:
        profiler.enable()
    timing = dict(generation_seconds=0., load_decode_seconds=0., input_hash_seconds=0.)
    usage_start = resource.getrusage(resource.RUSAGE_SELF)
    start = time.monotonic_ns()
    emit("harness", start_ns=start)
    before = time.monotonic_ns()
    manifest = validate_dataset(request["dataset"], spec) if request.get("dataset") else None
    timing["manifest_verification_seconds"] = (time.monotonic_ns() - before) / 1e9
    before = time.monotonic_ns()
    reader = (InputChunks(input_mode, spec["chunk_size"], dataset=request.get("dataset"),
                          size_bytes=manifest["size_bytes"] if manifest else None,
                          library=request.get("input_library")) if input_mode != "legacy" else None)
    timing["input_initialization_seconds"] = (time.monotonic_ns() - before) / 1e9
    before = time.monotonic_ns()
    engine = TopOfBookBacktester(**spec["configuration"])
    timing["initialization_seconds"] = (time.monotonic_ns() - before) / 1e9
    processing_ns = 0
    batch_metrics = Histogram() if diagnostic.get("batch_metrics") else None
    input_hash = hashlib.sha256()
    stream = (Path(request["dataset"]) / "events.bin").open("rb") if manifest and reader is None else None
    events = None
    try:
        last_heartbeat = start
        for offset in range(0, total_ticks, spec["chunk_size"]):
            if probes:
                checkpoint()
            elif CANCELLED:
                raise InterruptedError("benchmark cancelled")
            count = min(spec["chunk_size"], total_ticks - offset)
            before = stage_clock()
            events = (reader.read(offset, count, seed=spec["seed"]) if reader else
                      np.fromfile(stream, dtype=QUEUE_DTYPE, count=count) if stream else
                      make_queue_events(offset, count, seed=spec["seed"]))
            timing["load_decode_seconds" if manifest else "generation_seconds"] += (stage_clock() - before) / 1e9
            if len(events) != count:
                raise ValueError("truncated dataset")
            events.setflags(write=False)
            before = stage_clock()
            input_hash.update(memoryview(events).cast("B"))
            timing["input_hash_seconds"] += (stage_clock() - before) / 1e9
            if probes:
                emit("batch", processed=offset)
            before = time.monotonic_ns()
            processed = engine.process_queue_batch(events)
            native_end = time.monotonic_ns()
            elapsed_ns = native_end - before
            processing_ns += elapsed_ns
            if batch_metrics is not None:
                batch_metrics.add(elapsed_ns, processed)
            if probes or native_end - last_heartbeat >= 1_000_000_000:
                emit("progress", processed=offset + processed)
                last_heartbeat = native_end
            if elapsed_ns / 1e9 > LIMITS["batch"]:
                raise TimeoutError("native batch exceeded five seconds")
            if processed != count:
                raise AssertionError("input not fully processed")
            # Do not retain the previous chunk during allocation of the next one.
            events = None
        if stream and total_ticks == spec["ticks"] and stream.read(1):
            raise ValueError("dataset grew during replay")
    finally:
        if reader:
            # Release the worker's last array even if native processing failed.
            # InputChunks refuses to close or overwrite any surviving view.
            del events
            reader.close()
        if stream:
            stream.close()
    if manifest and total_ticks == spec["ticks"] and input_hash.hexdigest() != manifest["input_sha256"]:
        raise ValueError("dataset checksum mismatch")
    emit("materialize")
    before = time.monotonic_ns()
    result = engine.result()
    timing["result_materialization_seconds"] = (time.monotonic_ns() - before) / 1e9
    timing["processing_seconds"] = processing_ns / 1e9
    timing["execution_seconds"] = (timing["initialization_seconds"] + timing["processing_seconds"] +
                                    timing["result_materialization_seconds"])
    checkpoint()
    emit("verify")
    before = time.monotonic_ns()
    reconcile(result, spec["configuration"]["cash"])
    timing["ledger_verification_seconds"] = (time.monotonic_ns() - before) / 1e9
    before = time.monotonic_ns()
    hashed = hashlib.sha256()
    for name in ("positions", "orders", "fills", "queues"):
        hashed.update(memoryview(result[name]).cast("B"))
    scalars = {name: result[name] for name in ("processed", "cash", "equity", "net_pnl", "total_fees")}
    hashed.update(json.dumps(scalars, sort_keys=True).encode())
    timing["result_hash_seconds"] = (time.monotonic_ns() - before) / 1e9
    controls = dict(input_sha256=input_hash.hexdigest(), result_sha256=hashed.hexdigest(),
                    order_count=len(result["orders"]), fill_count=len(result["fills"]))
    if scalars["processed"] != total_ticks:
        raise AssertionError("processed count mismatch")
    expected_controls = EXPECTED.get(spec["scenario"], {}) if total_ticks == spec["ticks"] else {}
    for key, expected in expected_controls.items():
        if controls[key] != expected:
            raise AssertionError(f"canonical {key} mismatch")
    checkpoint()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    if profiler:
        profiler.disable()
        profiler.dump_stats(str(output / "python-profile.pstats"))
    if not probes:
        for name in ("generation_seconds", "load_decode_seconds", "input_hash_seconds"):
            timing[name] = None
    report = dict(schema=SCHEMA, contract=CONTRACT, status="ledger_reconciled", workload=spec,
                  diagnostic=diagnostic, measured_ticks=total_ticks, input_mode=input_mode,
                  batch_metrics=batch_metrics.snapshot() if batch_metrics is not None else None,
                  input_build=reader.library_identity if reader else None,
                  identity=observed, controls=controls, portfolio=scalars, positions=result["positions"].tolist(),
                  order_status_counts={str(n): int(np.count_nonzero(result["orders"]["status"] == n)) for n in range(5)},
                  timing=timing, warmup_seconds=warm_seconds, peak_rss_bytes=rss_bytes(),
                  resources=dict(cpu_seconds=usage.ru_utime + usage.ru_stime - usage_start.ru_utime - usage_start.ru_stime,
                                 minor_faults=usage.ru_minflt - usage_start.ru_minflt,
                                 major_faults=usage.ru_majflt - usage_start.ru_majflt,
                                 voluntary_context_switches=usage.ru_nvcsw - usage_start.ru_nvcsw,
                                 involuntary_context_switches=usage.ru_nivcsw - usage_start.ru_nivcsw,
                                 input_bytes=total_ticks * 88,
                                 result_array_bytes=sum(result[k].nbytes for k in ("positions", "orders", "fills", "queues")),
                                 allocation_count=None, hardware_counters=None),
                  validation=dict(ledger_reconciled=True, canonical_controls_checked=bool(expected_controls),
                                  full_volume_independent_trace_parity=False),
                  timing_boundary="prepared-chunk execution sum")
    emit("persist")
    before = time.monotonic_ns()
    write_json(output / "worker-result.json", report)
    persistence = (time.monotonic_ns() - before) / 1e9
    emit("complete", start_ns=start, persistence_seconds=persistence,
         harness_before_exit_seconds=(time.monotonic_ns() - start) / 1e9, peak_rss_bytes=rss_bytes())


def worker_main(request_path):
    global CANCELLED

    def stop(signum, frame):
        global CANCELLED
        CANCELLED = True

    signal.signal(signal.SIGTERM, stop)
    try:
        run_worker(read_json(request_path), Path(request_path).parent)
        return 0
    except BaseException as error:
        emit("error", error=type(error).__name__, message=str(error))
        return 1


def terminate(process, limits):
    start = time.monotonic()
    killed = False

    def signal_group(signum):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass

    signal_group(signal.SIGTERM)
    try:
        process.wait(timeout=limits["stop_grace"])
    except subprocess.TimeoutExpired:
        killed = True
        signal_group(signal.SIGKILL)
        try:
            process.wait(timeout=limits["cleanup"])
        except subprocess.TimeoutExpired:
            return dict(exited=False, seconds=time.monotonic() - start, forced_kill=killed,
                        process_group_kill_sent=True,
                        limit_seconds=limits["stop_grace"] + limits["cleanup"])
    # Contain a helper subprocess even if the worker exited before its helper.
    signal_group(signal.SIGKILL)
    return dict(exited=True, seconds=time.monotonic() - start, forced_kill=killed,
                process_group_kill_sent=True,
                limit_seconds=limits["stop_grace"] + limits["cleanup"])


def supervise(command_args, output, limits=None, cancel_file=None):
    """Independent parent watchdog; native worker calls cannot block it."""
    limits = dict(LIMITS if limits is None else limits)
    launched = time.monotonic_ns()
    phase, phase_at, progress_at = "startup", launched, launched
    harness_start = completed_at = None
    worker_started = None
    observed = None
    attested = False
    peak = 0
    reason = None
    complete = None
    pending = b""
    cleanup = None
    last_processed = 0
    with (output / "stderr.txt").open("xb") as errors, selectors.DefaultSelector() as selector:
        process = subprocess.Popen(command_args, stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
        assert process.stdout is not None
        os.set_blocking(process.stdout.fileno(), False)
        selector.register(process.stdout, selectors.EVENT_READ)
        probe = psutil.Process(process.pid)
        try:
            while True:
                for key, _ in selector.select(timeout=.02):
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    pending += block
                    if len(pending) > 1024 * 1024:
                        raise ValueError("worker protocol exceeds bounded buffer")
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        event = json.loads(line)
                        phase, phase_at = event["phase"], event["at_ns"]
                        if phase == "setup":
                            worker_started = phase_at
                        elif phase == "identity":
                            observed, attested = event["identity"], event["build_attested"]
                        elif phase == "harness":
                            harness_start = event["start_ns"]
                        elif phase == "progress":
                            progress_at = phase_at
                            last_processed = event.get("processed", last_processed)
                        elif phase == "complete":
                            complete, completed_at = event, phase_at
                            peak = max(peak, event["peak_rss_bytes"])
                        elif phase == "error":
                            reason = f"worker {event['error']}: {event['message']}"
                now = time.monotonic_ns()
                try:
                    peak = max(peak, probe.memory_info().rss)
                except psutil.NoSuchProcess:
                    pass
                if cancel_file and Path(cancel_file).exists():
                    reason = "cancelled by stop file"
                elif peak > limits["rss"]:
                    reason = "RSS limit exceeded"
                elif harness_start is None and (now - launched) / 1e9 > limits["setup"]:
                    reason = "setup/warm-up timeout"
                elif phase == "batch" and (now - phase_at) / 1e9 > limits["batch"]:
                    reason = "native batch timeout"
                elif harness_start and (now - harness_start) / 1e9 > limits["harness"]:
                    reason = "harness timeout"
                elif (now - progress_at) / 1e9 > limits["progress"]:
                    reason = "progress timeout"
                elif completed_at and (now - completed_at) / 1e9 > limits["cleanup"]:
                    reason = "worker exit timeout"
                if reason:
                    # A worker may have exited while its helper keeps stdout
                    # open. Contain the whole process group even after leader exit.
                    cleanup = terminate(process, limits)
                    break
                if process.poll() is not None and not selector.get_map():
                    break
        except KeyboardInterrupt:
            reason = "cancelled by interrupt"
            cleanup = terminate(process, limits)
        except Exception as error:
            reason = f"supervisor {type(error).__name__}: {error}"
            if process.poll() is None:
                cleanup = terminate(process, limits)
        finally:
            process.stdout.close()
            if process.poll() is None:
                cleanup = terminate(process, limits)
        ended = time.monotonic_ns()
    if reason is None and (process.returncode != 0 or complete is None or pending):
        reason = "worker exited without a complete successful protocol"
    if cleanup and not cleanup["exited"]:
        reason = f"{reason}; worker cleanup deadline exceeded"
    return dict(status="failed" if reason else "ok", failure_reason=reason, returncode=process.returncode,
                identity=observed, build_attested=attested, peak_rss_bytes=peak, complete=complete,
                python_startup_seconds=(worker_started - launched) / 1e9 if worker_started else None,
                setup_warmup_seconds=(harness_start - launched) / 1e9 if harness_start else None,
                harness_seconds=(ended - harness_start) / 1e9 if harness_start else None,
                process_seconds=(ended - launched) / 1e9, cleanup=cleanup,
                failure_summary=dict(last_phase=phase, last_reported_processed=last_processed,
                                     partial_progress_only=True) if reason else None,
                shutdown=dict(status="incomplete" if cleanup and not cleanup["exited"] else
                              "terminated" if cleanup else "exited",
                              returncode=process.returncode,
                              forced_kill=cleanup["forced_kill"] if cleanup else False,
                              process_group_kill_sent=cleanup["process_group_kill_sent"] if cleanup else False,
                              cleanup_seconds=cleanup["seconds"] if cleanup else 0.0,
                              cleanup_limit_seconds=limits["stop_grace"] + limits["cleanup"]))


def run_trial(output, spec, *, session_id, variant="candidate", python=sys.executable,
              dataset=None, cache_state="uncontrolled", cache_evidence=None, build_manifest=None,
              evidence=None, cancel_file=None, diagnostic=None, input_mode="legacy", input_library=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    validate_workload(spec)
    diagnostic = diagnostic_options(spec, diagnostic)
    if (input_mode not in MODES or (input_mode == "fused" and (dataset or not input_library)) or
            (input_mode in ("readinto", "mmap") and not dataset) or (input_mode != "fused" and input_library)):
        raise ValueError("input mode/library/dataset mismatch")
    source = dict(kind="storage" if dataset else "synthetic", cache_state=cache_state if dataset else "generated-chunks",
                  cache_evidence=cache_evidence if dataset else None,
                  manifest_sha256=digest(Path(dataset) / "manifest.json") if dataset else None)
    if dataset and diagnostic.get("prefix_ticks", spec["ticks"]) != spec["ticks"]:
        raise ValueError("storage diagnostic prefixes cannot verify the full dataset digest")
    request = dict(workload=spec, dataset=str(Path(dataset).resolve()) if dataset else None,
                   build_manifest=build_manifest, diagnostic=diagnostic, input_mode=input_mode,
                   input_library=str(Path(input_library).resolve()) if input_library else None)
    write_json(output / "request.json", request)
    before_conditions = observe_conditions()
    limits = dict(LIMITS)
    if dataset:
        limits["harness"] = limits["storage_total"] - limits["setup"]
    outcome = supervise([str(python), str(Path(__file__).resolve()), "_worker", str(output / "request.json")],
                        output, limits, cancel_file)
    measurement = None
    if outcome["status"] == "ok":
        try:
            report = read_json(output / "worker-result.json")
            if report["schema"] != SCHEMA or report["workload"] != spec or report["identity"] != outcome["identity"]:
                raise ValueError("worker report identity mismatch")
            measurement = dict(execution_seconds=report["timing"]["execution_seconds"],
                               harness_seconds=outcome["harness_seconds"],
                               cpu_seconds=report["resources"]["cpu_seconds"],
                               peak_rss_bytes=max(outcome["peak_rss_bytes"], report["peak_rss_bytes"]),
                               controls=report["controls"], persistence_seconds=outcome["complete"]["persistence_seconds"])
        except (OSError, KeyError, ValueError) as error:
            outcome.update(status="failed", failure_reason=f"invalid worker report: {error}")
    trial = dict(schema=SCHEMA, contract=CONTRACT, trial_id=str(output.resolve()),
                 session_id=session_id, variant=variant, status=outcome["status"],
                 diagnostic=diagnostic, input_mode=input_mode,
                 failure_reason=outcome["failure_reason"], workload=spec, source=source,
                 identity=outcome["identity"], reference_host_matches=bool(outcome["identity"] and reference_matches(outcome["identity"])),
                 build_attested=outcome["build_attested"], build_manifest=build_manifest,
                 session_evidence=evidence, conditions_before=before_conditions, conditions_after=observe_conditions(),
                 measurement=measurement, supervisor=outcome, limits=limits)
    write_json(output / "trial.json", trial)
    return trial


def compare_pairs(trials):
    """Screening signal only; deterministic bootstrap keeps uncertainty inspectable."""
    baseline = [t for t in trials if t["variant"] == "baseline"]
    candidate = [t for t in trials if t["variant"] == "candidate"]
    reason = None
    if len(baseline) != 30 or len(candidate) != 30 or any(t["status"] != "ok" for t in trials):
        reason = "requires 30 complete successful pairs"
    elif any(b["workload"] != c["workload"] or b["source"] != c["source"] or
             b["identity"]["host"] != c["identity"]["host"] or
             b["measurement"]["controls"] != c["measurement"]["controls"] for b, c in zip(baseline, candidate)):
        reason = "workload, host/runtime, source or output mismatch"
    elif any(len({json.dumps(t["identity"], sort_keys=True) for t in group}) != 1 for group in (baseline, candidate)):
        reason = "variant identity changed during screening"
    if reason:
        return dict(status="inconclusive", reason=reason)
    btimes = np.array([t["measurement"]["execution_seconds"] for t in baseline])
    ctimes = np.array([t["measurement"]["execution_seconds"] for t in candidate])
    if np.any(btimes <= 0):
        return dict(status="inconclusive", reason="nonpositive baseline timing")
    reductions = 1 - ctimes / btimes
    indices = np.random.default_rng(20260920).integers(0, 30, size=(10000, 30))
    low, high = np.quantile(np.median(reductions[indices], axis=1), [.025, .975]).tolist()
    controls = {}
    for metric in ("execution_seconds", "harness_seconds", "peak_rss_bytes"):
        b = distribution([t["measurement"][metric] for t in baseline])
        c = distribution([t["measurement"][metric] for t in candidate])
        controls[metric] = dict(baseline=b, candidate=c, p95_ratio=c["p95"] / b["p95"])
    benefit = float(np.median(reductions))
    acceptable = benefit >= .1 and low > 0 and all(v["p95_ratio"] <= 1.05 for v in controls.values())
    return dict(status="retention_signal" if acceptable else "no_retention_signal",
                median_execution_reduction=benefit, paired_bootstrap_95_interval=[low, high],
                bootstrap_seed=20260920, bootstrap_resamples=10000, comparisons=controls,
                interpretation="screening only; requires session, instrumentation and correctness review")


def campaign(args):
    if not 1 <= args.trials <= 200:
        raise ValueError("trials must be between 1 and 200")
    if args.baseline_python and args.trials != 30:
        raise ValueError("paired screening requires 30 trials per variant")
    spec = workload(args.scenario, args.chunk_size, args.ticks)
    evidence = session_evidence(args.session_evidence)
    build = read_json(args.build_manifest) if args.build_manifest else None
    baseline_build = read_json(args.baseline_build_manifest) if args.baseline_build_manifest else None
    if args.dataset:
        validate_dataset(args.dataset, spec)
    if args.cache_state != "uncontrolled" and (not args.dataset or not args.cache_evidence):
        raise ValueError("operator cache labels require a dataset and cache evidence")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "campaign.json", dict(schema=SCHEMA, contract=CONTRACT, workload=spec,
                                               planned_trials=args.trials, session_id=args.session_id,
                                               paired=bool(args.baseline_python), session_evidence=evidence,
                                               started_at=datetime.now(timezone.utc).isoformat()))
    trials = []
    for index in range(args.trials):
        variants = [("candidate", sys.executable, build)]
        if args.baseline_python:
            # Resolving a venv's python symlink would discard that environment.
            variants.insert(0, ("baseline", str(args.baseline_python.absolute()), baseline_build))
            if index % 2:
                variants.reverse()
        for label, python, build_record in variants:
            trial = run_trial(output / f"{index + 1:03d}-{label}", spec, session_id=args.session_id,
                              variant=label, python=python, dataset=args.dataset, cache_state=args.cache_state,
                              cache_evidence=args.cache_evidence, build_manifest=build_record,
                              evidence=evidence, cancel_file=args.cancel_file,
                              input_mode=getattr(args, "input_mode", "legacy"),
                              input_library=getattr(args, "input_library", None))
            trials.append(trial)
            print(json.dumps(dict(trial=index + 1, variant=label, status=trial["status"],
                                  measurement=trial["measurement"])), flush=True)
            if trial["status"] != "ok":
                break
        if trials[-1]["status"] != "ok":
            break
    summaries = {label: summarize([t for t in trials if t["variant"] == label])
                 for label in sorted({t["variant"] for t in trials})}
    write_json(output / "summary.json", dict(schema=SCHEMA, variants=summaries,
                                              paired_comparison=compare_pairs(trials) if args.baseline_python else None,
                                              campaign_complete=len(trials) == args.trials * (2 if args.baseline_python else 1)))
    return 0 if all(t["status"] == "ok" for t in trials) else 1


def aggregate(paths, output):
    trials, ids = [], set()
    for directory in paths:
        plan = read_json(directory / "campaign.json")
        records = sorted(directory.glob("*-*/trial.json"))
        if len(records) != plan["planned_trials"] or plan["paired"]:
            raise ValueError("aggregation requires complete unpaired campaigns; retain failures separately")
        for path in records:
            record = read_json(path)
            if record["trial_id"] in ids:
                raise ValueError("duplicate trial cannot increase qualification sample count")
            ids.add(record["trial_id"])
            trials.append(record)
    result = summarize(trials)
    write_json(output, result)
    return result


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "_worker":
        return worker_main(Path(sys.argv[2]))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("prepare", "run"):
        child = sub.add_parser(action)
        child.add_argument("--scenario", choices=SCENARIOS, default="fifo-smoke-v1")
        child.add_argument("--chunk-size", type=int, default=65521)
        child.add_argument("--ticks", type=int, help="bounded custom count for smoke only")
        child.add_argument("--output-dir", type=Path, required=True)
        if action == "run":
            child.add_argument("--trials", type=int, default=3)
            child.add_argument("--session-id", required=True)
            child.add_argument("--session-evidence", type=Path)
            child.add_argument("--build-manifest", type=Path)
            child.add_argument("--baseline-python", type=Path,
                               help="separate installed baseline environment; alternates 30 paired trials")
            child.add_argument("--baseline-build-manifest", type=Path)
            child.add_argument("--dataset", type=Path)
            child.add_argument("--input-mode", choices=MODES, default="legacy")
            child.add_argument("--input-library", type=Path, help="explicit benchmark-only fused generator library")
            child.add_argument("--cache-state", choices=("uncontrolled", "operator-cold", "operator-warm"), default="uncontrolled")
            child.add_argument("--cache-evidence")
            child.add_argument("--cancel-file", type=Path)
    summary = sub.add_parser("summarize")
    summary.add_argument("campaigns", type=Path, nargs="+")
    summary.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare_dataset(args.output_dir, workload(args.scenario, args.chunk_size, args.ticks))
            return 0
        if args.action == "summarize":
            result = aggregate(args.campaigns, args.output)
            print(json.dumps(result))
            return 1 if result["timing_gate"] == "fail" else 0
        return campaign(args)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
