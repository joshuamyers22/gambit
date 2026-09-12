Market data
===========

Frame contract
--------------

Gambit's labelled dataframe API is Polars. Keep timestamps in an explicit
``timestamp`` column, sort before calculating lagged values, and assign units at
the strategy boundary::

   bars = (
       pl.read_parquet("bars.parquet")
       .select("timestamp", "open", "high", "low", "close", "volume")
       .sort("timestamp")
       .with_columns(pl.col("close").pct_change().alias("return"))
   )

Do not silently coerce timezones or mix adjusted and unadjusted OHLC values.
Keep raw data immutable and derive a research frame with an explicit provenance
fingerprint.

Validation
----------

Validate inputs before feature computation::

   report = gambit.validate_market_data(
       bars,
       price_columns=("open", "high", "low", "close"),
       volume_columns=("volume",),
       calendar_name="NYSE",
       max_price_change=0.25,
   )
   report.raise_if_invalid()

The report distinguishes errors from warnings and does not mutate data. Large
changes may be real, so validation reports evidence rather than automatically
winsorizing or deleting observations.

Exchange calendars
------------------

``pandas-market-calendars`` supplies exchange schedules. It uses pandas
internally, but Gambit's public tabular boundary remains Polars. Calendar checks
answer whether records fall on valid sessions; they do not by themselves prove
that every expected bar is present within each session.

Missing data policy
-------------------

Choose a policy by economic meaning:

* Reject missing execution prices when an order cannot truthfully be filled.
* Preserve null factor warm-up periods and turn them into explicit false signals.
* Forward-fill only state variables known to remain valid until updated.
* Never backfill a feature from a future observation.

Experimental point-in-time access
---------------------------------

``PointInTimeMarketData`` is an optional owned interface for data whose
observation time differs from its publication time or which may be revised. It
accepts a Polars frame with exactly these columns:

* ``symbol`` and ``field`` identify a numeric series;
* ``observation_time`` is when the modeled event occurred;
* ``available_time`` is the first heartbeat allowed to observe that row;
* ``revision`` identifies that version of the observation; and
* ``value`` is a finite numeric value.

Rows with the same symbol, field, and observation time represent revisions and
must have distinct availability times. Reads are inclusive at the heartbeat: a
row whose availability equals ``as_of`` is visible. A scalar or window read
filters out later observations and later publications, then selects the latest
revision actually available at that heartbeat. Asking for an observation or
window after ``as_of`` fails rather than silently moving the clock.

For example::

   point_in_time = gambit.PointInTimeMarketData(
       bars.select(
           "symbol",
           pl.lit("price").alias("field"),
           pl.col("timestamp").alias("observation_time"),
           "available_time",
           "revision",
           pl.col("close").alias("value"),
       ),
       source="licensed-vendor-bars",
       dataset_revision="2026-09-12T18:00Z",
   )
   price_function = gambit.PointInTimePriceFunction(
       point_in_time,
       allow_previous=True,
       max_age=np.timedelta64(5, "m"),
   )
   published_price = gambit.PointInTimeIndicator(
       point_in_time,
       symbol="ESZ6",
       allow_previous=True,
       max_age=np.timedelta64(5, "m"),
   )
   strategy = gambit.Strategy(event_times, groups, price_function)
   strategy.add_indicator("published_price", published_price)

The price adapter uses each strategy heartbeat as both the requested observation
cutoff and the publication ``as_of`` time. ``allow_previous=True`` is a causal
last-known-value lookup, not interpolation; ``max_age`` bounds how long that
value may be reused. ``MarketDataAvailabilityPolicy.ERROR`` fails, ``WARN``
emits a runtime warning and returns a missing value, and ``SKIP`` returns a
missing value without a warning. Missing and stale observations have separate
policies.

The dataset fingerprint incorporates its canonical rows, source name, and
dataset revision. ``PointInTimePriceFunction`` and ``PointInTimeIndicator``
register that fingerprint in ``Strategy`` provenance automatically. Conflicting
automatic registrations using the same provenance name fail during stage
registration. For point-in-time datasets used only by custom indicators or
rules, register ``point_in_time.fingerprint`` explicitly with
``Strategy.record_input_fingerprint`` and pass the owned interface—not its full
frame—to callbacks.

``PointInTimeIndicator`` implements the built-in indicator-stage protocol. It
resolves each output independently with that heartbeat as both observation
cutoff and ``as_of`` time, so a delayed row cannot affect an earlier element.
The :download:`executable point-in-time strategy example
<../../examples/point_in_time_strategy.py>` demonstrates the stage and price
adapter using one owned dataset. The returned vector is causal element by
element, but a custom vectorized consumer can still combine a current element
with later elements; such callback logic remains outside the enforcement
boundary.

This interface is experimental. It does not localize timezones, infer vendor
publication times, fetch historical vintages, or prevent arbitrary callbacks
from retaining and reading external arrays. Normalize all timestamps to one
documented time basis before construction. Only code that performs reads through
this interface is covered by its causal-access guarantee.

Corporate actions and survivorship
----------------------------------

Gambit does not infer splits, dividends, delistings, symbol changes, or universe
membership. Normalize those upstream and preserve both the raw and adjusted
series. A current-constituent universe applied historically introduces
survivorship bias even when the backtest engine is mechanically correct.
