# Experimental conservative FIFO queue execution

Approved by Josh Myers on 2026-09-04. Reference: production-project-template
`e132c6e`, especially its latency-sensitive C++ guide and engineering defaults.
This extends only the experimental characterization backtester; existing
Strategy, market-mode replay, roll and deprecated stop-limit policies do not change.

## Contract

Create `TopOfBookBacktester(..., execution_model="fifo")` and pass aligned,
contiguous, one-dimensional `QUEUE_DTYPE` arrays to `process_queue_batch`.
The existing `process_batch(BOOK_DTYPE)` remains market-only; mixing APIs is
rejected. Inputs must remain immutable while native processing releases the GIL.

The 88-byte little-endian record contains a nested 64-byte `book` plus signed
64-bit `trade_price` and `trade_size`, signed 32-bit `aggressor`, and zero uint32
`reserved`. A record means **trade first, then post-trade top-of-book snapshot**.
Positive size requires positive price and aggressor -1 (sell) or +1 (buy).
No trade requires price, size and aggressor all zero. Feed ordering/staleness,
integer units, currency, overflow and failure rules are inherited from market mode.
Preprocessors must preserve individual print prices, sides and order; neither
aggregate future trades nor infer aggressor silently from an ambiguous snapshot.

For each instrument event, in global sequence order:

1. Validate the full record. Update the terminal bid mark.
2. For an already admitted order, an opposing trade at exactly its limit price
   first consumes `ahead`, then at most the order's remaining quantity. A trade
   that exactly exhausts volume ahead does **not** fill our order. Fill at the
   limit; shared cash and rounded-up fees are checked before each fill.
3. For an in-flight order, wait for the first subsequent instrument event whose
   receive time meets the latency floor. Admit it **after** that event's trade,
   behind the post-trade snapshot's same-side displayed volume. It cannot consume
   that trade, including when timestamps tie. The global sequence breaks ties.
4. Apply the alternating long/flat strategy. On rebalance, cancel any remaining
   order immediately and submit a new target-difference order if needed. Its
   fixed limit is this snapshot's bid for a buy or ask for a sell.

An arriving limit must still equal the same-side best and not cross the opposite
best. Otherwise reject with status 4. We do not guess an off-best initial queue
from top-of-book data or turn a post-only order into a market order. An admitted
order keeps its original limit and volume-ahead estimate if top prices move.
Only exact-price trades advance it: no inferred sweep/trade-through fills.

Quote-size decreases (including cancellations) never advance our position.
Increases never move ahead of us. This is deliberately conservative about
cancellations, **not** a claim of a lower bound on all real execution outcomes:
hidden liquidity, exchange rules, timing and adverse selection remain unmodeled.
One simulated order per instrument means a trade cannot be reused across multiple
own orders. Multiple own orders at one price and arbitrary order submissions are
not implemented. "Queue position" here is volume in lots ahead, not order rank.

Arrival uses the replay receive-time clock and a latency floor, not calibrated
exchange round-trip timing. Strategy cancellation is immediate after execution
on the event, with no cancel-ack latency. An arrived order can fill before a
same-event rebalance cancels its residual. These choices are part of this model,
not venue guarantees. Residual orders and positions remain at replay end.

## Audit and safety

The existing order/fill schemas are unchanged. FIFO results add an immutable,
isolated `queues` array aligned one-to-one with `orders`, containing:

- `limit_price`, final `ahead`, and `initial_ahead` (signed int64);
- `arrival_sequence` (uint64) and `arrival_time_ns` (int64).

Unadmitted orders retain arrival sentinels UINT64_MAX/-1 and zero queue amounts.
Queue state on a cancelled order is its last estimate, not an active queue.
Order statuses: 0 open/in-flight, 1 filled, 2 cancelled/replaced, 3 insufficient
cash, 4 unsupported/non-post-only arrival. No negative cash, shorts or leverage.

Queue audit reserves the same configured bound as order audit: 40 additional
bytes per admitted-or-submitted order slot; default 100,000, maximum 10 million.
All mutable state has one owner, with one nonblocking access guard per batch or
result call. No per-event allocations. Capacity exhaustion, malformed records,
or checked arithmetic failures invalidate the run permanently. Failed runs
cannot publish a partial success. Market-mode result schemas remain unchanged.

## Verification and measurement

`tests/test_fifo_backtest.py` implements an independent Python integer oracle
and compares every order, queue audit, fill and portfolio scalar. Hand-derived
fixtures supplement differential tests so two implementations cannot pass solely
by sharing an interpretation mistake. Tests include arrival-event exclusion,
zero/partial liquidity, ignored cancellations, additions behind us, price changes,
sell-side queues, equal timestamps, shared-cash sequence priority, cancellation,
latency, malformed data, overflow, bounded audit and chunk determinism.

`benchmarks/top_of_book_backtest.py --execution-model fifo` uses a separately
identified synthetic workload: 8 round-robin instruments, 10 aggregate records/s,
integer prices stable for 256 instrument observations, randomized opposing
trades and displayed sizes. Targets are 100 lots and rebalances occur every
10,000 observations by default. Use `--rebalance-events 16` for a dense
cancel/replacement stress test; audit capacity bounds the supported horizon.
This is not exchange-calibrated crypto data and not a general trading strategy.

Full-volume ledger reconciliation and repeat hashes complement, but do not
replace, smaller independent Python trace parity. Execution timing sums native
calls, initialization and result copies; input generation, hashing, ledger
checking, storage loading and most caller-loop overhead are outside that timer.
Report the full harness wall time separately. Do not infer a production p95
from a handful of local runs or claim the existing few-second objective met
without the measured evidence.
