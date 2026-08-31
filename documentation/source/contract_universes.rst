Contract universes
==================

Gambit can construct sector-sized or market-sized collections in one validated
operation. A universe contains two read-only indexes: groups by name and
contracts by globally unique symbol. Each :class:`~gambit.ContractGroup` keeps
its contracts in insertion order, so configuration and reports remain
deterministic even with thousands of instruments.

Equity sectors
--------------

Use :class:`~gambit.ContractGroupSpec` to apply common metadata to every symbol
in a sector. Plain strings are sufficient when contracts share the defaults.

.. code-block:: python

   import gambit as gb

   us_equity = gb.InstrumentSpec(
       asset_class=gb.AssetClass.EQUITY,
       currency="USD",
       exchange_calendar="NYSE",
       trading_timezone="America/New_York",
   )

   universe = gb.create_contract_groups(
       {
           "equities/technology": gb.ContractGroupSpec(
               ["AAPL", "MSFT", "NVDA", "AVGO"],
               instrument_spec=us_equity,
           ),
           "equities/energy": gb.ContractGroupSpec(
               ["XOM", "CVX", "COP"],
               instrument_spec=us_equity,
           ),
           "equities/banks": gb.ContractGroupSpec(
               ["JPM", "BAC", "WFC"],
               instrument_spec=us_equity,
           ),
       }
   )

   technology = universe.group("equities/technology")
   aapl = universe.contract("AAPL")
   assert aapl.contract_group is technology

Futures sectors
---------------

Use :class:`~gambit.ContractSpec` when expiry, multiplier, or other values vary
by contract. Values on a contract override shared group defaults.

.. code-block:: python

   import numpy as np
   import gambit as gb

   cme_future = gb.InstrumentSpec(
       asset_class=gb.AssetClass.FUTURE,
       currency="USD",
       tick_size=0.25,
       exchange_calendar="CME_Equity",
       trading_timezone="America/Chicago",
   )

   universe = gb.create_contract_groups(
       {
           "futures/equity-index": gb.ContractGroupSpec(
               [
                   gb.ContractSpec("ESZ6", expiry=np.datetime64("2026-12-18")),
                   gb.ContractSpec(
                       "NQZ6",
                       expiry=np.datetime64("2026-12-18"),
                       multiplier=20,
                       properties={"root": "NQ", "sector": "equity-index"},
                   ),
               ],
               multiplier=50,
               instrument_spec=cme_future,
           ),
       }
   )

   assert universe.contract("ESZ6").multiplier == 50
   assert universe.contract("NQZ6").multiplier == 20

Large generated universes
-------------------------

Input collections may be lists, tuples, or generators. Construction is linear
in the number of contracts; lookups by symbol or group name are constant-time.
The full request is checked for malformed metadata, duplicate requested
symbols, and collisions with registered symbols before groups or contracts are
created.

.. code-block:: python

   symbols = (f"STOCK-{number:05d}" for number in range(10_000))
   universe = gb.create_contract_groups({"equities/research": symbols})
   assert len(universe.contracts) == 10_000

Symbols are globally unique, including across groups. Clear the registries only
between independent research runs or tests; clearing the contract cache also
removes contract references held by every cached group.

Strategy integration
--------------------

Register all universe groups with a builder in one call. Rules and indicators
can then target a sector by passing the corresponding group.

.. code-block:: python

   builder = gb.StrategyBuilder()
   builder.add_contract_universe(universe)

   research_group = universe.group("equities/research")
   # builder.add_indicator("momentum", indicator, contract_groups=[research_group])

For a very large universe, bulk creation removes Python configuration
boilerplate but does not make per-symbol callbacks free. Prefer vectorized
Polars feature construction, restrict rules to active signals, and benchmark
the full strategy rather than registry creation alone.
