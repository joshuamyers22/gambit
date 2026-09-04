"""Experimental native top-of-book backtest prototype, not a Strategy replacement.

The built-in long-only strategy alternates each instrument's target between
``target_lots`` and zero every ``rebalance_events`` observations of that instrument.
By default it submits market orders, with at most one active order per instrument.
Orders first become fillable on a subsequent event for that instrument, after
the configured receive-time latency. Partial fills consume the opposing quote's
size for that event; each input quote supplies a refreshed liquidity budget.
The default market mode has no queue position. Neither mode models persistent
own-market impact, leverage, shorting,
funding, external risk-policy callback, or forced terminal liquidation.

Price is integer ticks; size/position is integer lots. Cash, fees, equity and P&L
use the common monetary unit ``price_tick_value * lot_size``. Inputs must share
that unit and quote currency; FX conversion is not implicit. Fees round up.
Remaining long positions are marked at the last observed bid, with no exit fee.

Book inputs are immutable during native processing, one-dimensional, contiguous,
aligned arrays with BOOK_DTYPE. Global sequence starts at zero and is contiguous;
receive time is nondecreasing. Feed age is bounded by ``maximum_feed_age_ns``
(default one second). Invalid events or capacity/overflow failures
invalidate the run; results cannot be published from that instance afterward.

Opt-in ``execution_model='fifo'`` uses ``process_queue_batch(QUEUE_DTYPE)``.
Each record is an opposing trade followed by a post-trade top-of-book snapshot.
Aggressor is -1 for a sell, +1 for a buy, or 0 with zero price/size for no trade.
The strategy posts a limit at its own side's best price at submission. Arrival
occurs after the first subsequent instrument event meeting receive-time latency;
it joins behind that snapshot's displayed size and cannot consume its trade.
Non-best or crossing arrivals are rejected (order status 4). Only later opposing
trades at exactly the limit price reduce volume ahead and then fill our order.
Quote changes/cancellations never reduce volume ahead; later additions join
behind us. Off-best resting orders retain their queue estimate. No inferred
trade-through fills, depth reconstruction, hidden liquidity, or actual venue
queue-position claims. Strategy cancellation is immediate after event execution.
One active order per instrument, not multiple simulated orders at one price.
``result()['queues']`` aligns with orders and records limit price, remaining and
initial volume ahead, and arrival sequence/time (UINT64_MAX/-1 until admitted).
"""

import numpy as np

try:
    from gambit._factor_cache import TopOfBookBacktester
except ImportError:  # pragma: no cover - optional native dependency
    TopOfBookBacktester = None

BOOK_DTYPE: np.dtype = np.dtype(
    [("sequence", "<u8"), ("event_time_ns", "<i8"), ("receive_time_ns", "<i8"),
     ("bid", "<i8"), ("ask", "<i8"), ("bid_size", "<i8"), ("ask_size", "<i8"),
     ("instrument_id", "<u4"), ("flags", "<u4")], align=True,
)

QUEUE_DTYPE: np.dtype = np.dtype(
    [("book", BOOK_DTYPE), ("trade_price", "<i8"), ("trade_size", "<i8"),
     ("aggressor", "<i4"), ("reserved", "<u4")], align=True,
)
