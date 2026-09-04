"""Experimental native top-of-book backtest prototype, not a Strategy replacement.

The built-in long-only strategy alternates each instrument's target between
``target_lots`` and zero every ``rebalance_events`` observations of that instrument.
It submits market orders only, with at most one active order per instrument.
Orders first become fillable on a subsequent event for that instrument, after
the configured receive-time latency. Partial fills consume the opposing quote's
size for that event; each input quote supplies a refreshed liquidity budget.
There is no queue position, persistent own-market impact, leverage, shorting,
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
