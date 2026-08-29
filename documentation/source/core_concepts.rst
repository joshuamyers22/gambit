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
