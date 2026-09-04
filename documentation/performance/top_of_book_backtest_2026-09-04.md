# Experimental top-of-book backtest — 2026-09-04

The native prototype executes a complete **characterization strategy** with
orders, partial fills, shared cash, fees, positions and terminal P&L. It is not
yet a general Strategy replacement or proof of a production strategy's speed.

## Final implementation measurements

| Horizon | Events | Orders | Partial-fill records | Measured execution |
| --- | ---: | ---: | ---: | ---: |
| 2023–2024 | 631,584,000 | 63,154 | 433,526 | 0.876 s |
| 2023–2025 | 946,944,000 | 94,690 | 649,979 | 1.312 s |

Execution includes initialization, every native batch call, and copying the
final positions/order/fill arrays into the result. The two measurements are
individual runs, **not** an established p95 or a portable performance promise.

Both full fill ledgers reconcile exactly to cash, fees, and positions. The
three-year final result hash is
`668f3426e34b9b49b868eadb561c9fd787509369a6e4c04d061dfe9cef8f339c`.
The final input hash is
`db763cf4272772fc4a5e4940708391a9d58fb5cdf7ab19825a0dca0374ac0446`.

The earlier implementation was run three times with chunk sizes 65,521, 65,536,
and 65,521. All three produced these identical input and result hashes, at
1.699, 1.693 and 1.697 seconds. Stale-feed and terminal-valuation failure guards
were then added and the final implementation was measured separately above;
the result hash remained unchanged. Do not combine timings from these different
builds into one latency distribution.

## What was actually simulated

- Eight instrument IDs, 10 aggregate quote events/second, three calendar years
  including the 2024 leap day, synthetic seed `20260904`.
- The compiled strategy alternates each instrument between long 1,000 lots and
  flat every 10,000 observations of that instrument. This is a low-order-rate
  characterization workload, not a proposed profitable strategy and not
  market-making on every tick.
- Market orders only, at most one active order per instrument. A newly submitted
  order cannot execute until a subsequent event for that instrument and at least
  1 ms of receive-time latency. Same-timestamp events follow global sequence order.
- Buy at ask, sell at bid; each fill is capped by that event's opposing size.
  Liquidity is refreshed per input quote; persistent own-market impact and queue
  position are not modeled.
- Shared nonnegative cash and long-only positions; insufficient cash rejects
  the remaining order. Fees are 100 parts per million, rounded up in integer
  monetary units. Remaining longs are marked at the last observed bid, with no
  forced liquidation or fabricated exit fee.
- Price tick = 0.0001 quote currency, lot = 0.001 asset units, so one monetary
  unit = 0.0000001 quote currency. The common scales/currency are part of the
  synthetic contract, not a substitute for real instrument metadata.
- The final implementation rejects feed age beyond one second, gaps, reordering,
  invalid book values, capacity exhaustion, and integer overflow. Failed runs
  cannot publish a valid result.
- Limits: no limit/roll orders, arbitrary user strategy, shorts/leverage, funding,
  FX, external risk-policy callbacks, queue model, or own-market impact.

## Measurement boundary and hardware

Apple M4, 10 logical CPUs, **24 GiB RAM**, macOS 15.5, Apple Clang 17.0.0
(`clang-1700.0.13.5`), C++11, `-O3`, Python 3.10.20, NumPy 2.2.6.
No manual SIMD, affinity, fast-math, GPU, or multithreaded portfolio processing.

The benchmark is **synthetic chunk-resident replay**, not an entirely resident
three-year dataset or cold disk load. A chunk is generated, hashed, then supplied
to the native engine. Generation, hashing and ledger verification are outside
the execution timer. The full final three-year benchmark took **61.94 seconds**,
including **41.65 seconds of generation**, and peaked at **284,327,936 bytes RSS**.

The current 64-byte layout represents 60.6 GB over three years and cannot reside
entirely in the test machine's 24 GiB RAM. A compact preprocessed representation,
real loading/decompression performance, and a real strategy still need validation
before claiming a general few-second backtest from prepared data. Summed native
batch timings do not measure all caller-loop overhead or real storage stalls.

## Correctness evidence and remaining acceptance work

Unit tests independently implement the model in Python and compare **every
order, fill, cash balance, position, fee total, equity and P&L exactly** on seeded
multi-instrument corpora. They cover chunk boundaries, same-event exclusion,
partial/zero liquidity, shared-cash rejection, fees, latency, stale data,
malformed layouts, audit capacity, overflow and immutable snapshot isolation.

Full-volume runs reconcile all recorded fills against the final portfolio and
compare deterministic result hashes across chunk boundaries. They have **not**
yet been compared event-for-event with an independent Python engine at the full
946.9-million-event volume. Identical native replay hashes are not independent
proof of execution-model correctness. Run reference parity for the actual
strategy before promoting this prototype.

The acceptance brief and scope decisions are in
`../architecture/native_tick_backtest.md` and `../architecture/adr_native_tick_replay.md`.
References follow production-project-template `e132c6e`; no external backtesting
framework was used as an implementation reference.

## Reproduce and inspect

```sh
uv pip install --python .venv/bin/python --no-deps -e .
uv run pytest tests/test_top_of_book_backtest.py tests/test_top_of_book_benchmark.py -q
uv run python benchmarks/top_of_book_backtest.py --ticks 631584000 --output /tmp/top-book-2y-new.json
uv run python benchmarks/top_of_book_backtest.py --output /tmp/top-book-3y-new.json
```

Raw final results, including configuration and native/source/benchmark hashes:

- [Two years](top_of_book_backtest_2026-09-04_final_2y.json)
- [Three years](top_of_book_backtest_2026-09-04_final_3y.json)

Earlier-build repetition evidence is retained in the matching `trial1.json`,
`trial2.json`, and `trial3.json` files; those are not the final build's timings.
