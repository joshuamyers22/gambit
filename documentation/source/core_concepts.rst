Core concepts
=============

Event time
----------

A strategy owns an ordered NumPy ``datetime64`` heartbeat. Index ``i`` means the
same event time to indicators, signals, rules, simulators, pricing, and
accounting. Inputs should be sorted, unique, and expressed at a resolution that
matches the simulated decision process.

Data moves through the strategy in this order:

#. Indicators transform market data into features.
#. Signals transform indicators and parent signals into decision states.
#. Rules inspect a signal, account state, and open orders, then propose orders.
#. Risk policies accept, resize, or reject each proposal.
#. Market simulators turn eligible orders into trades after the configured lag.
#. Accounting updates positions, realized P&L, unrealized P&L, and equity.

This separation is central to adversarial review: a researcher can identify
which component introduced an assumption and test it independently.

Contracts and contract groups
-----------------------------

A :class:`gambit.Contract` represents a tradable instrument. A
:class:`gambit.ContractGroup` is a stable analytical role whose concrete
contracts may change through time. Futures research might use groups named
``front_future`` and ``hedge`` even as the symbols roll.

Contracts are process-cached by symbol to prevent ambiguous duplicate objects.
Tests and isolated research runs should clear both registries before creating a
new instrument universe::

   gambit.Contract.clear_cache()
   gambit.ContractGroup.clear_cache()

Price functions
---------------

A price function receives ``(contract, timestamps, index, context)`` and returns
one floating-point price. It is used for execution estimates, marking positions,
and sizing. Included adapters cover dictionaries and sorted arrays. Custom price
functions are appropriate for contract rolls, currencies, or synthetic baskets.

Indicators, signals, and rules
------------------------------

Indicators and signals are vector calculations. Rules are event calculations
because they depend on account state. Dependency names make the stage graph
inspectable and allow Gambit to reject missing dependencies and cycles before a
run begins.

Position filters are a convenience, not risk management. ``zero``, ``nonzero``,
``positive``, and ``negative`` determine whether a rule is called. Risk policies
remain responsible for order and portfolio constraints.

Orders, trades, and execution lag
---------------------------------

An order expresses intent; a trade records a simulated fill. A nonzero
``trade_lag`` models the minimum delay between observing a bar and reaching the
market. Daily research should usually use a lag of at least one unless the input
bar is known before its timestamped execution price.

Market orders do not guarantee realistic prices. The simple simulator fills at
the supplied price function and can apply slippage, commission, and fees. For
intraday or illiquid research, supply a simulator whose fill logic uses bid/ask,
volume, latency, order type, and partial-fill assumptions.

Immutable results and provenance
--------------------------------

``Strategy.run()`` returns a :class:`gambit.BacktestResult` containing detached
Polars tables, risk artifacts, stage telemetry, configuration, and provenance.
Persisted bundles are written atomically and validated by digest, schema, and row
count when loaded. Record every external input that should affect reproducibility::

   strategy.record_polars_input("features", feature_frame)
   result = strategy.run()
   result.save("research/run-001.gambit")

Bounded result loading
~~~~~~~~~~~~~~~~~~~~~~

``BacktestResult.load`` checks the entire bundle before decoding any table:
bounded manifest reads, typed metadata, exact table filenames, SHA-256 digests,
Arrow schema and record-batch dimensions, buffer references, and allocation
estimates. It reads regular, non-symlink member files into bounded byte snapshots;
decoding does not reopen paths that another writer could replace. Duplicate JSON
keys, malformed metadata, unsupported layouts, and budget violations raise
``BacktestBundleError``. Hashes detect changes, not who authored a bundle.

The default ``BundleLoadLimits`` policy allows a 1 MiB manifest, 64 MiB per table,
256 MiB across table files, 1 million rows per table, 4 million rows in total,
128 columns per table, 1,024 batches per table, and 1 MiB of IPC metadata per
table. Estimated decoded payload is capped at 256 MiB per table and 512 MiB
overall. The estimate includes per-cell conversion headroom, referenced buffers,
and logical expansion of string views. It is not a hard RSS guarantee: byte
snapshots, Python/native allocator overhead, and library state also consume
memory. Use an OS-isolated process for strict memory/time ceilings, and keep
Polars patched; this preflight is not a complete native-decoder security audit.

Override individual positive-integer limits for a reviewed workload::

   limits = gambit.BundleLoadLimits(
       max_table_rows=2_000_000,
       max_total_rows=8_000_000,
   )
   restored = gambit.BacktestResult.load("research/run-001.gambit", limits=limits)

The accepted Arrow profile is little-endian, uncompressed, flat columns:
8–64-bit integers, 32/64-bit floats, booleans, strings/binary (offset or view
layouts), nulls, day-resolution dates, nanosecond time, ms/us/ns datetime and
duration, and 128-bit decimals. Nested/list/struct, categorical/dictionary,
extension/custom-metadata, compressed, and other encodings are rejected rather
than passed to an unbounded decoder. There is no unsafe bypass flag. Normalize
unsupported custom analytics in a trusted environment before storing them in a
bundle intended for this loader.

Versions 2, 3, and 4 remain readable within this profile and the chosen limits;
older schema columns are preserved without inventing missing values. The file
format and writer are unchanged by these limits: saving a very large result or
unsupported custom table does not guarantee admission by the default loader.
The profile follows the `Arrow columnar format and IPC metadata specifications
<https://arrow.apache.org/docs/format/Columnar.html>`_.
