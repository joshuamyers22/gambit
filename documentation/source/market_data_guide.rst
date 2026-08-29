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

Corporate actions and survivorship
----------------------------------

Gambit does not infer splits, dividends, delistings, symbol changes, or universe
membership. Normalize those upstream and preserve both the raw and adjusted
series. A current-constituent universe applied historically introduces
survivorship bias even when the backtest engine is mechanically correct.
