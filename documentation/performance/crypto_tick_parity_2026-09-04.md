# Three-year synthetic crypto tick parity — 2026-09-04

Result: **PASS** for the native C++ `TickRing` / `TickFactorProcessor` against a
stateful pure-Python version of the existing tick-factor reference. This tests
tick-factor arithmetic and ring accounting, not all Gambit C++ code, an exchange
order book, FIFO P&L, or a complete trading strategy.

## Workload and method

- 10 aggregate ticks/second, continuously from January 1, 2023 through December
  31, 2025: **946,944,000 ticks**, including the 2024 leap day.
- Eight synthetic instrument IDs, fractional quantities, varying positive
  prices, spreads, intraday/monthly cycles, timestamp jitter, and receive latency.
  The aggregate rate is shared across instruments, not 10 ticks/second each.
- Counter-based generation with seed `20260904`; chunk boundaries do not change
  the input bytes. This is volume equivalence, **not exchange-calibrated data**.
- Both implementations actually processed every tick in identical order.
  Native and Python cumulative snapshots were compared after all **14,454
  chunks**, with exact additional checkpoints at two and three calendar years.
- Chunk size 65,521; ring capacity 65,536; native drain size 4,093. Non-power-of-two
  chunk/drain sizes exercise repeated ring wraparound and partial final batches.
- Integer fields require exact equality. Floating fields require finite results
  and `rel_tol=1e-12`, `abs_tol=1e-12`.
- The streaming Python oracle was separately checked against the repository's
  existing reference on sequence gaps, zero prices/quantities, and negative
  receive-latency inputs. Those hostile cases are not inserted into the main run.

## Measured results

| Cumulative horizon | Ticks | C++ processing | Python processing | Result |
| --- | ---: | ---: | ---: | --- |
| Two years | 631,584,000 | 24.73 s | 147.53 s | Pass; checkpoint values exactly equal |
| Three years | 946,944,000 | 37.09 s | 220.63 s | Pass; checkpoint values exactly equal |

- C++: **25.53 million ticks/s**; Python: **4.29 million ticks/s**.
- Measured processing speedup: **5.95×**.
- Complete replay: **303.78 seconds**, including both implementations, input
  generation, hashing, and comparisons. Generation alone took 27.96 seconds.
- Peak process RSS: **157,155,328 bytes** (149.88 MiB). The streamed records
  represented **60,604,416,000 bytes** (60.60 GB); no full tick dataset was saved.
- Pushed and popped: exactly 946,944,000 each; dropped ticks, residual depth,
  and sequence errors: **zero**. Eight instruments were observed.

All integer metrics agreed at every comparison. Total quantity, mean spread,
mean mid-price, and mean absolute return also agreed exactly at every comparison.
Total notional had a maximum intermediate absolute difference of
**0.00048828125**, always inside the specified tolerance. It matched exactly at
both annual checkpoints. Thus this run demonstrates numerical parity within the
declared tolerance, not universal bit-for-bit identity of intermediate sums.

## Timing limitations

This is one sequential, interleaved replay on Apple Silicon, macOS 15.5,
Python 3.10.20, NumPy 2.2.6—not a portable performance guarantee or a confidence
interval. The million-tick pilot served as a preliminary correctness/runtime check.

The C++ timer includes ring pushes and draining through the factor processor.
The Python timer includes conversion of input columns to Python scalars and the
equivalent sequential factor loop, without queue transport. Generation, hashing,
and snapshot comparison are outside both processing timers. The result does not
measure concurrent producer/consumer behavior, parsing/network/disk throughput,
real-time tail latency, or exchange bursts. The 5.95× ratio applies to these
measured paths, not to an entire trading system.

## Evidence and reproduction

Raw measurements and both checkpoint snapshots:
[`crypto_tick_parity_2026-09-04.json`](crypto_tick_parity_2026-09-04.json).

```bash
uv run pytest tests/test_crypto_tick_parity_benchmark.py tests/test_native_reference.py -q
uv run python benchmarks/crypto_tick_parity.py --rate 10 --output /tmp/gambit-crypto-replay-new.json
```

The runner requires a compiled native extension, refuses to overwrite an
existing output file, and exits with an assertion failure on a parity or ring
accounting mismatch. `--ticks 1000000` selects the shorter pilot workload.

- Native source revision: `a9fd11140de89fb091b471b0490d8e7fca2f5e7d`.
- Tick input SHA-256: `47f519badc19ac4f3216146216b5bea24d7f625d8afe2ea9e1a781746e1dadbd`.
- Native extension SHA-256: `dd76cf22e22e0c1b66a1d8e8249cd2294d7bfaf792d342a68b9d4b0f234facd1`.
- `tick_ring.cpp` SHA-256: `537b02ad451c7f16b22098d7f44bbf3d19f85ab0643a238db9fb4cb425206460`.

No production C++ or Python implementation was changed for this test.
