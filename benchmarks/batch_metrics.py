"""Bounded diagnostic summaries; no I/O or per-record clocks on the native path."""

from __future__ import annotations

import math
import threading
from collections import deque


class Histogram:
    """65 fixed buckets: zero, then powers-of-two upper bounds in nanoseconds.

    Quantiles are bucket upper bounds, never exact percentiles. Max is exact.
    One observation describes a batch (or a producer/consumer batch overlap).
    """

    def __init__(self):
        self.buckets = [0] * 65
        self.count = self.records = self.total_ns = self.maximum_ns = 0

    def add(self, nanoseconds, records=0):
        if (type(nanoseconds) is not int or not 0 <= nanoseconds < 2**64 or
                type(records) is not int or records < 0):
            raise ValueError("invalid bounded timing observation")
        self.buckets[nanoseconds.bit_length()] += 1
        self.count += 1
        self.records += records
        self.total_ns += nanoseconds
        self.maximum_ns = max(self.maximum_ns, nanoseconds)

    def snapshot(self):
        quantiles = {}
        for label, fraction in (("p50", .5), ("p95", .95), ("p99", .99)):
            rank = math.ceil(self.count * fraction)
            cumulative = 0
            quantiles[label + "_upper_bound_ns"] = None
            for index, count in enumerate(self.buckets):
                cumulative += count
                if self.count and cumulative >= rank:
                    quantiles[label + "_upper_bound_ns"] = (1 << index) - 1
                    break
        return dict(count=self.count, records=self.records, total_ns=self.total_ns,
                    maximum_ns=self.maximum_ns if self.count else None,
                    buckets=list(self.buckets), **quantiles)


class HandoffMetrics:
    """Single producer/consumer sidecar, bounded by queue capacity plus one batch.

    Reserve before publication so a fast consumer can always find its timestamp.
    Complete after native processing; a popped but not yet reported batch can
    coexist with a full queue. Never hold this lock across native calls or waits.
    Snapshot only after both workers have stopped.
    """

    def __init__(self, capacity, batch):
        self.limit = capacity + batch
        self.pending = deque()
        self.pending_records = self.pending_high_water = 0
        self.lock = threading.Lock()
        self.producer = Histogram()
        self.consumer = Histogram()
        self.age = Histogram()
        self.scheduled_age = Histogram()
        self.admission = Histogram()
        self.sampled_high_water = 0
        self.full_wait_episodes = self.full_wait_polls = 0
        self.full_observations = 0

    def reserve(self, count, stamp, scheduled):
        if type(count) is not int or count <= 0:
            raise ValueError("reservation must contain a positive integer record count")
        with self.lock:
            if self.pending_records + count > self.limit:
                raise RuntimeError("batch diagnostics capacity exceeded")
            self.pending.append([count, stamp, scheduled])
            self.pending_records += count
            self.pending_high_water = max(self.pending_high_water, self.pending_records)
        if scheduled is not None:
            self.admission.add(max(0, stamp - scheduled), count)

    def complete(self, count, stamp):
        if type(count) is not int or count < 0:
            raise ValueError("completion must contain a non-negative integer record count")
        with self.lock:
            if count > self.pending_records:
                raise RuntimeError("completion has no admitted batch timestamp")
            self.pending_records -= count
            while count:
                entry = self.pending[0]
                used = min(count, entry[0])
                self.age.add(stamp - entry[1], used)
                if entry[2] is not None:
                    self.scheduled_age.add(stamp - entry[2], used)
                count -= used
                entry[0] -= used
                if entry[0] == 0:
                    self.pending.popleft()

    def snapshot(self):
        return dict(schema="gambit-batch-diagnostics-v1", diagnostic_only=True,
                    age_boundary="enqueue call entry to consumer batch completion",
                    age_weighting="one observation per producer/consumer batch overlap; records counted separately",
                    high_water_kind="sampled lower bound", sampled_high_water=self.sampled_high_water,
                    full_observations=self.full_observations,
                    full_wait_episodes=self.full_wait_episodes, full_wait_polls=self.full_wait_polls,
                    pending_records=self.pending_records, pending_high_water=self.pending_high_water,
                    pending_record_limit=self.limit, pending_intervals=len(self.pending),
                    producer_batches=self.producer.snapshot(), consumer_batches=self.consumer.snapshot(),
                    enqueue_to_completion=self.age.snapshot(), scheduled_to_completion=self.scheduled_age.snapshot(),
                    admission_lateness=self.admission.snapshot())
