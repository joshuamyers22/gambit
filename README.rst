|PyVersion| |Status| |License|

Introduction
============

The ``gambit`` package is designed for backtesting quantitative strategies. It was originally built for my own use after I could not find a python based framework that was fast, extensible and transparent enough for use in my work.

The goals are:

* Speed - Performance sensitive components are written at the numpy level, or in cython or C++, which can lead to performance gains of a couple of orders of magnitude over Python code.
* Transparency - If you are going to commit money to a strategy, you want to know exactly what assumptions it includes. The code is written and documented so these are as clear as possible.
* Extensibility - It would be impossible to think of all requirements for backtesting strategies that traders could come up with. In addition, it's important to measure custom metrics relevant to the strategy being traded.

Using this framework, you can:

* Create indicators, trading signals, trading rules and market simulators and add them to a strategy
* Create contract groups for PNL grouping. For example, for futures and options, you may create a "front-month future" and "delta hedge" where the actual instruments change over time but you still want to analyze PNL at the contract group level.
* Reuse existing market simulation or add your own assumptions to simulate when and at what price orders are filled
* Measure returns, drawdowns, common return metrics such as sharpe, calmar and also add your own metrics.
* Optimize your strategy's parameters taking advantage of all the CPUs on a machine

Gambit uses Polars for tabular inputs and outputs. Timestamps remain ordinary,
explicit columns rather than an implicit dataframe index.
Statsmodels remains the analytics backend for regression/statistical routines,
and pandas-market-calendars remains the source of exchange schedules. Pandas may
therefore be installed transitively, but it is not Gambit's dataframe API.

Market-data validation
----------------------

``validate_market_data`` checks a Polars DataFrame without modifying it. It can
report missing or unordered timestamps, invalid prices and volumes, large price
changes, future records, and dates outside a named exchange calendar. Validation
returns structured findings so callers can distinguish errors from warnings and
decide whether to reject a run::

   report = gambit.validate_market_data(
       bars,
       price_columns=("close",),
       volume_columns=("volume",),
       calendar_name="NYSE",
       max_price_change=0.25,
   )
   report.raise_if_invalid()

Pre-trade risk policies
-----------------------

Risk policies run after a rule proposes an order and before a market simulator
can fill it. Policies are composable and every proposal produces an immutable
``OrderDecision`` for later audit::

   builder.add_risk_policy(gambit.MaxOrderQuantity(100))
   builder.add_risk_policy(gambit.MaxPositionQuantity(500))

Rejected orders remain in strategy order history with cancelled status, while
``strategy.order_decisions`` records the responsible policy and reason code.




Installation
------------

Gambit requires Python 3.10 or newer, a C/C++ compiler, and libzip. On macOS,
install libzip with ``brew install libzip``; on Debian/Ubuntu use
``apt install libzip-dev``. Then install the project in an isolated environment:

::

   python3.12 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .

For development, install every test, documentation, and notebook dependency:

::

   python -m pip install -r requirements-dev.txt
   python -m pytest

Repository layout
-----------------

* ``src/gambit`` contains the installable package and native sources.
* ``tests`` contains automated regression and strategy tests.
* ``examples/notebooks`` contains executable examples and sample data.
* ``tools/migrate_notebooks.py`` records the deterministic pandas-to-Polars
  example migration and clears generated notebook output.
* ``documentation`` contains Sphinx sources and previously generated docs.

Documentation
-------------

The best way to get started is the local ``examples/notebooks/getting_started.ipynb`` notebook.

See ``CONTRIBUTING.md`` for the full validation workflow and
``ADVERSARIAL_REVIEW_PLAN.md`` for the current hardening roadmap.

Disclaimer
----------

The software is provided on the conditions of the simplified BSD license.

.. _Python: http://www.python.org

.. |PyVersion| image:: https://img.shields.io/badge/python-3.10+-blue.svg
   :alt:

.. |Status| image:: https://img.shields.io/badge/status-beta-green.svg
   :alt:

.. |License| image:: https://img.shields.io/badge/license-BSD-blue.svg
   :alt:
