import hashlib
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np

root = Path('/Users/jkm0607/Projects/gambit')
sys.path.insert(0, str(root / 'benchmarks'))
from tick_ring_benchmark import make_ticks
from gambit.factor_cache import TickRing
import gambit._factor_cache as native

records = make_ticks(65536)
rows = []
for trial in range(100):
    ring = TickRing(65536)
    entered = threading.Event()
    accepted = []
    def produce():
        entered.set()
        accepted.append(ring.push_batch(records))
    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    assert entered.wait(1)
    begin = time.perf_counter_ns()
    ring.close()
    elapsed = time.perf_counter_ns() - begin
    producer.join(1)
    assert not producer.is_alive() and len(accepted) == 1
    output = ring.pop_batch(65536)
    assert np.array_equal(output, records[:accepted[0]])
    assert ring.push_batch(records[:1]) == 0
    metrics = ring.metrics
    assert metrics['pushed'] == metrics['popped'] == accepted[0]
    assert metrics['dropped'] == 65537 - accepted[0] and metrics['depth'] == 0
    rows.append(dict(trial=trial, close_ns=elapsed, accepted=accepted[0]))
print(json.dumps(dict(rows=rows, native_sha256=hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
                     p50_p95_p99_max_ns=np.percentile([r['close_ns'] for r in rows], [50,95,99,100]).tolist()), indent=2))
