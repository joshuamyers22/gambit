Gambit documentation
====================

**Version:** |release| · **Install:** ``python -m pip install gambit-markets``

Gambit is a transparent, event-driven research and backtesting library. It uses
Polars for tabular data, NumPy at strategy boundaries, and native C++/Cython for
selected execution, pricing, tick, and factor-cache paths.

Start here
----------

Getting started
   Build a complete 4/16 moving-average strategy from deterministic data and
   inspect trades, positions, and returns.

User guide
   Learn Gambit's data model, staged strategy lifecycle, execution semantics,
   risk controls, reproducibility, and native factor cache.

Examples
   Adapt practical recipes for signals, execution costs, calendars, risk,
   statistical analysis, and factor research.

API reference
   Find public classes and functions by domain, with signatures and docstrings.

The guiding principle is explicit simulation. Data timing, order timing, market
fills, costs, risk decisions, and result provenance are visible objects rather
than hidden framework behavior.

.. toctree::
   :maxdepth: 2

   installation
   getting_started
   user_guide
   examples
   api_reference
   platform_support
   testing

Project documentation
---------------------

* `Source repository <https://github.com/joshuamyers22/gambit>`_
* `Issue tracker <https://github.com/joshuamyers22/gambit/issues>`_
* `Changelog <https://github.com/joshuamyers22/gambit/blob/main/CHANGELOG.md>`_
* `API stability policy <https://github.com/joshuamyers22/gambit/blob/main/API_STABILITY.md>`_

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
