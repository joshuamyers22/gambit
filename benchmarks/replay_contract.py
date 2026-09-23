"""Versioned workload admission and report decisions for controlled FIFO replay."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

CONTRACT = "gambit-fifo-latency-v1"
SCHEMA = "gambit-controlled-replay-v1"
MAX_CHUNK = 1_048_576
RSS_LIMIT = 512 * 1024**2
CONFIG = dict(instruments=8, cash=10**13, target_lots=100, rebalance_events=10000,
              fee_ppm=100, latency_ns=1_000_000, audit_capacity=1_000_000,
              maximum_feed_age_ns=1_000_000_000, execution_model="fifo")
SCENARIOS = {
    "fifo-3y-sparse-v1": (946_944_000, 10000),
    "fifo-2y-sparse-v1": (631_584_000, 10000),
    "fifo-1m-dense-v1": (1_000_000, 16),
    "fifo-2m-dense-v1": (2_000_000, 16),
    "fifo-smoke-v1": (100_003, 16),
}
EXPECTED = {
    "fifo-3y-sparse-v1": {
        "input_sha256": "8e03b1e7d62ff6683a055718ef7818cf5188a56bf48084a1df1fb7095369465f",
        "result_sha256": "5e663f1117533205af50d1297faae98a791b720dff2215386fecb09272c8b2d6",
        "order_count": 88776, "fill_count": 595838,
    },
    "fifo-1m-dense-v1": {
        "input_sha256": "96530b9ef5198f10e8adb20b5625f30870c66315459e61ea3d4ce3310e0e6c48",
        "result_sha256": "4b1ffefc0e49767189903b42dcec606073770705de34f8de9222deecfd720747",
        "order_count": 55365, "fill_count": 95485,
    },
    # LAT-02 native regression controls; not an independent policy oracle.
    "fifo-2m-dense-v1": {
        "input_sha256": "4935e45d31409271c60b4bea27e2f8ac0026de83160caf54e02aede2745ea035",
        "result_sha256": "2f27fc9c11e94c7dca71e819f8148fb4d33732cbf71facf3407e3e22853a1250",
        "order_count": 110491, "fill_count": 190412,
    },
}
LIMITS = dict(setup=10.0, batch=5.0, progress=30.0, harness=120.0,
              storage_total=600.0, stop_grace=1.0, cleanup=2.0, rss=RSS_LIMIT)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("JSON manifest exceeds 1 MiB")
    return json.loads(path.read_text(), parse_constant=lambda value: fail(f"nonfinite JSON: {value}"))


def fail(message):
    raise ValueError(message)


def write_json(path, value):
    """Exclusive creation: retained reports are never overwritten, even on retry."""
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def workload(scenario, chunk_size=65521, ticks=None):
    if scenario not in SCENARIOS:
        raise ValueError("unknown scenario")
    if type(chunk_size) is not int or not 1 <= chunk_size <= MAX_CHUNK:
        raise ValueError("chunk size must be in [1, 1048576]")
    count, interval = SCENARIOS[scenario]
    if ticks is not None:
        if scenario != "fifo-smoke-v1" or type(ticks) is not int or not 1 <= ticks <= 1_000_000:
            raise ValueError("custom ticks are permitted only for bounded smoke runs")
        count = ticks
    return dict(scenario=scenario, ticks=count, chunk_size=chunk_size, seed=20260904,
                record_bytes=88, aggregate_records_per_second=10,
                configuration=dict(CONFIG, rebalance_events=interval))


def validate_workload(value):
    expected = workload(value["scenario"], value["chunk_size"],
                        value["ticks"] if value["scenario"] == "fifo-smoke-v1" else None)
    if value != expected:
        raise ValueError("workload differs from approved configuration")


def distribution(values):
    if not values:
        return None
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("invalid timing/resource observation")
    values = sorted(values)
    def quantile(q):
        return values[max(0, math.ceil(q * len(values)) - 1)]
    return dict(count=len(values), p50=quantile(.5), p95=quantile(.95), p99=quantile(.99),
                maximum=values[-1], jitter=values[-1] - quantile(.5), p99_9=None)


def compatibility_key(trial):
    """Exact runtime/workload/build identity; session notes are evidence, not identity."""
    values = {key: trial[key] for key in ("workload", "source", "identity", "variant")}
    values["diagnostic"] = trial.get("diagnostic", {})
    values["measurement_profile"] = trial.get("measurement_profile")
    return json.dumps(values, sort_keys=True)


def summarize(trials):
    if not trials:
        raise ValueError("at least one trial required")
    successes = [t for t in trials if t["status"] == "ok"]
    keys = {compatibility_key(t) for t in trials}
    sessions = {}
    for trial in trials:
        sessions[trial["session_id"]] = sessions.get(trial["session_id"], 0) + 1
    execution = [t["measurement"]["execution_seconds"] for t in successes]
    wall = [t["measurement"]["harness_seconds"] for t in successes]
    rss = [t["measurement"]["peak_rss_bytes"] for t in successes]
    reasons = []
    if any(t.get("input_mode", "legacy") != "legacy" for t in trials):
        reasons.append("experimental input pipelines require separate qualification")
    if any(t.get("diagnostic") for t in trials):
        reasons.append("diagnostic profiling/probe modes cannot qualify")
    if any(t.get("measurement_profile") != "acceptance-v1" for t in trials):
        reasons.append("requires explicit acceptance-v1 measurement profile")
    if len({t["trial_id"] for t in trials}) != len(trials):
        reasons.append("duplicate trial IDs")
    if len(keys) != 1:
        reasons.append("mixed workload, source, host, build or variant identities")
    if len(successes) != len(trials):
        reasons.append("failed/cancelled trials retained")
    if len(trials) != 200 or sorted(sessions.values()) != [50] * 4:
        reasons.append("requires exactly four distinct sessions of 50 trials")
    if any(t["workload"]["scenario"] != "fifo-3y-sparse-v1" or t["source"]["kind"] != "synthetic"
           or t["workload"]["chunk_size"] != 65521 for t in trials):
        reasons.append("not the primary synthetic/default-chunk workload")
    if any(not t["reference_host_matches"] for t in trials):
        reasons.append("reference host/runtime does not match or could not be observed")
    if any(not t["build_attested"] for t in trials):
        reasons.append("exact native build commands not attested")
    if any(not t["session_evidence"] for t in trials):
        reasons.append("session conditions not supplied")
    if any(t["identity"] is None for t in trials):
        reasons.append("missing runtime identity")
    if successes and len({json.dumps(t["measurement"]["controls"], sort_keys=True) for t in successes}) != 1:
        reasons.append("input/result controls differ between trials")
    if any(t["measurement"]["controls"] != EXPECTED["fifo-3y-sparse-v1"] for t in successes):
        reasons.append("primary canonical controls not verified")
    timing = "ineligible" if reasons else "pass"
    if not reasons and (sum(t > 5 for t in execution) > 4 or max(execution) > 7.5
                        or sum(t > 75 for t in wall) > 4 or max(wall) > 90 or max(rss) > RSS_LIMIT):
        timing = "fail"
    return dict(schema=SCHEMA, contract=CONTRACT, trials=len(trials), successful_trials=len(successes),
                failed_trials=len(trials) - len(successes), sessions=sessions,
                execution_seconds=distribution(execution), harness_seconds=distribution(wall),
                peak_rss_bytes=distribution(rss), timing_gate=timing, ineligibility_reasons=reasons,
                qualification="pending_independent_evidence",
                remaining_evidence=["independent full-volume trace parity", "same-commit platform/sanitizer gates",
                                    "acceptance-profile and session independence review", "owner disposition"],
                p99_descriptive_only=True, p99_9_qualified=False)
