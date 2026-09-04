# Conservative FIFO exchange-queue test — 2026-09-04

The approved queue model is implemented and tested. **The proposed few-second
target is not met:** the three-year native execution measurements are 9.08–9.15 s.
These results are separate from the earlier market-order-only 1.31 s result;
both the execution model and synthetic workload differ.

## Measured results

Eight instruments, 10 aggregate trade-plus-quote records per second, seed
20260904. The horizons are volume equivalents, including a leap day, not actual
historical data; timestamps use a zero synthetic origin.

| Workload | Records | Orders | Fill records | Native execution | Full harness |
| --- | ---: | ---: | ---: | ---: | ---: |
| Two years | 631,584,000 | 59,208 | 397,202 | 6.081 s | 39.42 s |
| Three years, trial 1 | 946,944,000 | 88,776 | 595,838 | 9.105 s | 59.01 s |
| Three years, trial 2 | 946,944,000 | 88,776 | 595,838 | 9.150 s | 58.93 s |
| Three years, trial 3 | 946,944,000 | 88,776 | 595,838 | 9.084 s | 59.17 s |
| Dense cancel/replace | 1,000,000 | 55,365 | 95,485 | 0.0123 s | 0.269 s |

The three-year median is 9.105 s, not a demonstrated production p95. Chunk sizes
were 65,521, 65,536 and 65,521. All three runs produced identical input and
result hashes, including the complete queue audit and order/fill ledgers:

- Input: `8e03b1e7d62ff6683a055718ef7818cf5188a56bf48084a1df1fb7095369465f`
- Result: `5e663f1117533205af50d1297faae98a791b720dff2215386fecb09272c8b2d6`

Each full-volume fill ledger reconciled exactly to cash, fees and positions.
Three-year orders: 82,864 filled; 5,912 rejected because the submitted price was
no longer eligible for best-price post-only admission; none open, cancelled or
cash-rejected at termination. Final positions are all zero, cash/equity
9,966,405,721,267 units and fees 33,602,509,933 units. Synthetic P&L is a mechanics
check, not evidence of profitability or calibrated execution accuracy.

The dense run rebalances every 16 observations rather than 10,000. It exercises
40,713 cancellations/replacements, 3,502 arrival rejections and 8 terminal open
orders. Do not extrapolate its short-run timing to a full three-year dense
workload: audit capacity and memory requirements must be budgeted first.

## What was tested

The fixed strategy alternates long 100 lots and flat. It posts at the same-side
best, joins behind displayed volume after latency, and fills only after later
opposing exact-price trades exhaust that volume. Quote-size decreases do not
advance the queue; additions join behind us. Arrival-event trades are excluded.
There is only one simulated order per instrument; no multiple-own-order FIFO,
hidden liquidity, market impact, funding, arbitrary strategy or actual venue
queue reconstruction. The exact policy is in
[`fifo_queue_execution.md`](../architecture/fifo_queue_execution.md).

The synthetic generator uses integer prices stable for 256 instrument
observations, round-robin IDs, randomized aggressor/size, and displayed sizes
between 50 and 150 lots. Targets are 100 lots, receive-time latency is 1 ms,
fees are 100 ppm, and cash starts at 10^13 common monetary units. Price tick
0.0001 and lot 0.001 imply monetary unit 0.0000001 common quote currency, as
in the earlier prototype. This workload is not exchange-calibrated crypto data.

46 FIFO-specific tests plus two FIFO benchmark tests were added. Independent
Python integer replay compares every order, queue state, fill and portfolio
value on randomized corpora and on 100,003-record benchmark corpora at both
sparse and dense order rates. Hand-calculated fixtures test both sides, queue
exhaustion, partial fills, cancellation/re-entry, equal timestamps, latency,
wrong-side/wrong-price trades, price changes, shared-cash sequence priority,
fee boundaries, invalid records, layout, audit exhaustion and overflow.
**Full 946.9-million-record Python execution parity has not been run.**

Local verification: 772 tests passed, Ruff, mypy, coverage policy, package build
and strict C++ warnings checks passed. The FIFO test file is included in the
hosted ASan/UBSan job. Hosted results must be checked for the eventual commit;
passing local tests are not a substitute for those checks.

## Timing boundary and provenance

Apple M4, 10 logical CPUs, 24 GiB RAM; macOS 15.5 ARM64; Python 3.10.20,
NumPy 2.2.6, Apple Clang 17.0.0, existing C++11 / `-O3` build. No manual SIMD,
affinity, fast-math or multithreaded portfolio processing. This is a local
development machine, not an isolated production benchmarking host; small test
runs were performed during part of the repeated measurements.

Execution sums initialization, native batch calls and final result copies.
Generation (about 24 s at three years), hashing, Python ledger checks, real
storage loading and most caller-loop overhead are **outside** that timer. Full
harness time is about 59 s. Peak process RSS across the three-year runs was
262,324,224 bytes. Chunk-resident input is not a fully resident dataset: 946,944,000
88-byte records represent 83,331,072,000 logical bytes, exceeding this machine's
RAM. Real loading/decompression and actual strategy acceptance remain outstanding.

All runs use native binary SHA256
`3ce0d1803db18518ada5f4aae60d1fe893e1fdb7ff0643e970521087eeb4bb90`,
C++ source SHA256
`015888396d9f0685c3fe6e0e58d805a2d5105790dd47d31dfedd76111dc2c702`,
and benchmark SHA256
`e572922f98383d72a5dc7cb7c11938cf17cf0e0f9ee496c308aa1387f7a528ba`.
Raw reports are the adjacent `fifo_backtest_2026-09-04_*.json` files. A separate
10-million-record market-mode smoke run also reconciled successfully; its short
timing is not a controlled before/after performance comparison.

## Reproduce

```sh
uv pip install --python .venv/bin/python --no-deps -e .
uv run pytest -q tests/test_fifo_backtest.py tests/test_top_of_book_benchmark.py
uv run python benchmarks/top_of_book_backtest.py --execution-model fifo --output /tmp/fifo-3y-new.json
uv run python benchmarks/top_of_book_backtest.py --execution-model fifo --ticks 631584000 --output /tmp/fifo-2y-new.json
uv run python benchmarks/top_of_book_backtest.py --execution-model fifo --ticks 1000000 --rebalance-events 16 --output /tmp/fifo-dense-new.json
```

The harness refuses to overwrite existing reports. Reference authority remains
production-project-template `e132c6e` and the approved execution contract, not an
external backtesting framework. Next performance work should profile validation,
event dispatch and data movement without weakening queue or accounting rules.
