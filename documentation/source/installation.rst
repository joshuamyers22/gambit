Installation
============

Binary installation
-------------------

Install the distribution from PyPI. The distribution is named
``gambit-markets`` while the Python package remains ``gambit``::

   python -m pip install --upgrade pip
   python -m pip install gambit-markets

Verify both names explicitly::

   from importlib.metadata import version
   import gambit

   print(version("gambit-markets"))
   print(gambit.Strategy)

Supported systems
-----------------

Release wheels target CPython 3.10, 3.11, and 3.12 on Linux x86-64 and macOS x86-64
and ARM64. Windows users should use WSL. A source installation additionally
requires a C/C++ compiler, Python development headers, and libzip.

Source installation
-------------------

On Debian or Ubuntu::

   sudo apt-get update
   sudo apt-get install -y build-essential libzip-dev python3-dev

On macOS::

   brew install libzip

Then create an isolated environment and install Gambit::

   git clone https://github.com/joshuamyers22/gambit.git
   cd gambit
   python3.12 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -e .

Optional dependencies
---------------------

The base installation contains the NumPy/Polars backtesting, accounting,
execution, factor-cache, and historical-risk paths. Install only the optional
capabilities a deployment uses::

   python -m pip install "gambit-markets[calendars]"      # exchange sessions
   python -m pip install "gambit-markets[persistence]"    # HDF5
   python -m pip install "gambit-markets[research]"       # SciPy and Statsmodels
   python -m pip install "gambit-markets[visualization]"  # Plotly and widgets
   python -m pip install "gambit-markets[all]"            # all runtime extras

Optional modules load on demand. For example, importing ``gambit`` and running
a core backtest does not import Plotly or Statsmodels. Calling an unavailable
feature raises an ``ImportError`` naming the extra to install.

Contributor extras are separate::

   python -m pip install -e ".[notebooks]"  # executable notebooks
   python -m pip install -e ".[docs]"       # Sphinx documentation
   python -m pip install -e ".[dev]"        # tests, lint, typing, packaging

For a complete contributor environment::

   python -m pip install -e ".[dev,docs,notebooks]"

Native dependency diagnostics
-----------------------------

An ``ImportError`` mentioning ``libzip`` usually means pip built from the source
distribution because no compatible wheel was available. Install the system
dependency above, or use one of the supported Python/platform combinations.

Confirm the native modules load::

   import gambit._io
   import gambit._options
   import gambit.compute_pnl

On Linux and macOS, the factor-cache extension should also load::

   import gambit._factor_cache
