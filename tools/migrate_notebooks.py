"""One-time, deterministic migration of example notebooks from pandas to Polars."""

from __future__ import annotations

from pathlib import Path

import nbformat

ROOT = Path(__file__).parents[1] / "examples" / "notebooks"


def replace(path: Path, replacements: list[tuple[str, str]]) -> None:
    notebook = nbformat.read(path, as_version=4)
    for cell in notebook.cells:
        if cell.cell_type != "code":
            continue
        for old, new in replacements:
            cell.source = cell.source.replace(old, new)
        cell.outputs = []
        cell.execution_count = None
    nbformat.write(notebook, path)


COMMON = [
    ("import pandas as pd", "import polars as pl"),
    ("pd.DataFrame", "pl.DataFrame"),
    (".values", ".to_numpy()"),
    (".sort_values(by=", ".sort("),
]


def migrate_data_creation_notebook() -> None:
    path = ROOT / "data" / "create_data.ipynb"
    notebook = nbformat.read(path, as_version=4)
    notebook.cells[0].source = '''from pathlib import Path

import h5py
import numpy as np
import polars as pl

import gambit as pq


def create_data(source: Path, destination: Path) -> pl.DataFrame:
    """Create the compact example options dataset from a local source archive."""
    frames: list[pl.DataFrame] = []

    with h5py.File(source, "r") as archive:
        dates = np.arange(np.datetime64("2023-01-01"), np.datetime64("2023-01-10"))
        for date in dates:
            date_text = np.datetime_as_string(date, unit="D").replace("-", "_")
            key = f"D_{date_text}"
            if key not in archive:
                continue

            trades = (
                pq.hdf5_to_df(source, key)
                .with_columns(
                    pl.date("okey_yr", "okey_mn", "okey_dy").alias("expiry"),
                    ((pl.col("uBid") + pl.col("uAsk")) * 0.5).alias("umid"),
                )
                .select(
                    "timestamp",
                    pl.col("okey_cp").alias("put_call"),
                    "expiry",
                    pl.col("okey_xx").alias("strike"),
                    pl.col("prtPrice").alias("price"),
                    pl.col("prtVolume").alias("volume"),
                    pl.col("prtIv").alias("iv"),
                    pl.col("prtDe").alias("delta"),
                    "umid",
                )
                .with_columns(
                    pl.concat_str(
                        pl.col("put_call").str.slice(0, 1),
                        pl.col("strike").cast(pl.Int64).cast(pl.String),
                        pl.col("expiry").cast(pl.String),
                        separator="-",
                    ).alias("symbol"),
                    pl.col("timestamp").cast(pl.Datetime("ns")),
                )
                .sort(["symbol", "timestamp"])
            )
            bars = (
                trades.group_by_dynamic("timestamp", every="5m", group_by="symbol")
                .agg(
                    pl.col("price").first().alias("o"),
                    pl.col("price").max().alias("h"),
                    pl.col("price").min().alias("l"),
                    pl.col("price").last().alias("c"),
                    pl.col("volume").sum().alias("v"),
                    pl.col("umid").last(),
                    pl.col("iv").last(),
                    pl.col("delta").last(),
                )
                .filter(pl.col("c").is_finite())
                .select("timestamp", "symbol", "o", "h", "l", "c", "v", "umid", "iv", "delta")
            )
            frames.append(bars)

    result = pl.concat(frames) if frames else pl.DataFrame()
    result.write_csv(destination)
    return result
'''
    for cell in notebook.cells:
        if cell.cell_type == "code":
            cell.outputs = []
            cell.execution_count = None
    nbformat.write(notebook, path)


migrate_data_creation_notebook()


replace(
    ROOT / "getting_started.ipynb",
    COMMON
    + [
        (
            "aapl = pd.read_csv(aapl_file)[['timestamp', 'c']]\naapl.timestamp = pd.to_datetime(aapl.timestamp)",
            "aapl = pl.read_csv(aapl_file, try_parse_dates=True).select('timestamp', 'c')",
        ),
        (
            "aapl['date'] = aapl.timestamp.to_numpy().astype('M8[D]')",
            "aapl = aapl.with_columns(pl.col('timestamp').dt.date().alias('date'))",
        ),
        (
            "aapl['overnight_ret'] = np.where(aapl.date > aapl.date.shift(1), aapl.c / aapl.c.shift(1) - 1, np.nan)",
            "aapl = aapl.with_columns(pl.when(pl.col('date') > pl.col('date').shift(1)).then(pl.col('c') / pl.col('c').shift(1) - 1).otherwise(None).alias('overnight_ret'))",
        ),
        (
            "aapl['overnight_ret_negative'] = (aapl.overnight_ret < 0)",
            "aapl = aapl.with_columns((pl.col('overnight_ret') < 0).alias('overnight_ret_negative'))",
        ),
        (
            "aapl['eod'] = np.where(aapl.date.shift(-2) > aapl.date, True, False)",
            "aapl = aapl.with_columns((pl.col('date').shift(-2) > pl.col('date')).fill_null(False).alias('eod'))",
        ),
        (
            "aapl['bod_price'] = np.where(aapl.date > aapl.date.shift(1), aapl.c, np.nan)\naapl.bod_price = aapl.bod_price.ffill()",
            "aapl = aapl.with_columns(pl.when(pl.col('date') > pl.col('date').shift(1)).then(pl.col('c')).otherwise(None).forward_fill().alias('bod_price'))",
        ),
        ("aapl['symbol'] = 'AAPL'", "aapl = aapl.with_columns(pl.lit('AAPL').alias('symbol'))"),
        (
            "aapl['stop'] = (aapl.c < (aapl.bod_price * 0.99))",
            "aapl = aapl.with_columns((pl.col('c') < pl.col('bod_price') * 0.99).alias('stop'))",
        ),
        ("aapl.symbol.to_numpy()", "aapl['symbol'].to_numpy()"),
        ("aapl.timestamp.to_numpy()", "aapl['timestamp'].to_numpy()"),
        ("aapl.c.to_numpy()", "aapl['c'].to_numpy()"),
    ],
)

replace(
    ROOT / "multiple_contracts.ipynb",
    COMMON
    + [
        (
            "df = pl.DataFrame({'timestamp': on_ret.timestamps, 'price': on_ret.prices})\n        df['date'] = df.timestamp.to_numpy().astype('M8[D]')\n        df['contract'] = on_ret.name",
            "df = pl.DataFrame({'timestamp': on_ret.timestamps.astype('M8[ns]'), 'price': on_ret.prices}).with_columns(pl.col('timestamp').dt.date().alias('date'), pl.lit(on_ret.name).alias('contract'))",
        ),
        ("prices = pd.concat(dfs)", "prices = pl.concat(dfs)"),
        (
            "prices = prices[['timestamp', 'contract', 'date', 'price']]",
            "prices = prices.select('timestamp', 'contract', 'date', 'price')",
        ),
        (
            "prices = pd.read_csv(filename, usecols=['timestamp', 'c'])\n        prices.timestamp = pd.to_datetime(prices.timestamp)\n        prices['date'] = prices.timestamp.to_numpy().astype('M8[D]')",
            "prices = pl.read_csv(filename, columns=['timestamp', 'c'], try_parse_dates=True).with_columns(pl.col('timestamp').dt.date().alias('date'))",
        ),
        (
            "prices['on_ret'] = np.where(prices.date > prices.date.shift(1), prices.c / prices.c.shift(1) - 1, np.nan)",
            "prices = prices.with_columns(pl.when(pl.col('date') > pl.col('date').shift(1)).then(pl.col('c') / pl.col('c').shift(1) - 1).otherwise(None).alias('on_ret'))",
        ),
        (
            "date_rets = prices[np.isfinite(prices.on_ret) & (prices.on_ret > 0)]",
            "date_rets = prices.filter(pl.col('on_ret').is_finite() & (pl.col('on_ret') > 0))",
        ),
        ("date_rets.date.to_numpy()", "date_rets['date'].to_numpy()"),
        ("date_rets.on_ret.to_numpy()", "date_rets['on_ret'].to_numpy()"),
        ("date_prices = prices[prices.date == date]", "date_prices = prices.filter(pl.col('date') == date)"),
        ("date_prices.timestamp.to_numpy()", "date_prices['timestamp'].to_numpy()"),
        ("date_prices.c.to_numpy()", "date_prices['c'].to_numpy()"),
        (
            "first = prices.sort(['timestamp']).drop_duplicates(subset=['contract', 'date'])\n    first['enter'] = True\n    first = first[['timestamp', 'contract', 'enter']]\n    prices = pd.merge(prices, first, on=['timestamp', 'contract'], how='left')\n    prices.enter = prices.enter.fillna(False)\n    prices['eod'] = np.where(prices.date.shift(-2) > prices.date, True, False)",
            "first = prices.sort('timestamp').unique(subset=['contract', 'date'], keep='first').select('timestamp', 'contract').with_columns(pl.lit(True).alias('enter'))\n    prices = prices.join(first, on=['timestamp', 'contract'], how='left').with_columns(pl.col('enter').fill_null(False), (pl.col('date').shift(-2) > pl.col('date')).fill_null(False).alias('eod'))",
        ),
        ("prices['stop'] = np.full(len(prices), True)", "prices = prices.with_columns(pl.lit(True).alias('stop'))"),
        ("on_rets.to_numpy()()", "on_rets.values()"),
    ],
)

replace(
    ROOT / "reporting.ipynb",
    COMMON
    + [
        ("from types import SimpleNamespace\n", ""),
        ("df_data[np.isfinite(df_data.equity)].head()", "df_data.filter(pl.col('equity').is_finite()).head()"),
    ],
)

replace(
    ROOT / "optimizing_strategies.ipynb",
    [
        ("import numpy as np\nimport gambit as pq", "import numpy as np\nimport polars as pl\nimport gambit as pq"),
        ("returns_df = strategy.df_returns().set_index('timestamp')", "returns_df = strategy.df_returns()"),
        ("returns = returns_df.ret.values", "returns = returns_df['ret'].to_numpy()"),
        ("equity = returns_df.equity.values", "equity = returns_df['equity'].to_numpy()"),
        ("dates = returns_df.index.values", "dates = returns_df['timestamp'].to_numpy()"),
        ("max_processes=8", "max_processes=1"),
        ("df[df.stop_pct == -0.002]", "df.filter(pl.col('stop_pct') == -0.002)"),
        ("for stop_pct in np.arange(-0.01, -0.002, 0.001):", "for stop_pct in [-0.002]:"),
        ("for ret_threshold in np.arange(0.001, 0.004, 0.001):", "for ret_threshold in [0.002]:"),
        ("for stop_pct in np.arange(-0.001, -0.005, -0.001):", "for stop_pct in [-0.002]:"),
        ("for ret_threshold in np.arange(0, 0.006, 0.001):", "for ret_threshold in [0.002]:"),
    ],
)

replace(
    ROOT / "options_trading.ipynb",
    COMMON
    + [
        (
            "_expiries = prices[['date', 'expiry']].sort(['date', 'expiry']).drop_duplicates()\n    dates = np.unique(_expiries.date.to_numpy().astype('M8[D]'))\n    expiry = _expiries.expiry.to_numpy().astype('M8[D]')",
            "_expiries = prices.select('date', 'expiry').sort(['date', 'expiry']).unique(maintain_order=True)\n    dates = np.unique(_expiries['date'].to_numpy().astype('M8[D]'))\n    expiry = _expiries['expiry'].to_numpy().astype('M8[D]')",
        ),
        ("expiry[_expiries.date == date]", "expiry[_expiries['date'].to_numpy() == date]"),
        ("_strikes = prices[prices.strike % 100 == 0]", "_strikes = prices.filter(pl.col('strike') % 100 == 0)"),
        (
            "keys = _strikes[['date', 'put_call', 'expiry']].sort(\n        by=['date', 'put_call', 'expiry']).drop_duplicates()",
            "keys = _strikes.select('date', 'put_call', 'expiry').sort(['date', 'put_call', 'expiry']).unique(maintain_order=True)",
        ),
        (
            "_strikes = _strikes[['date', 'put_call', 'expiry', 'strike']].sort(\n        by=['date', 'put_call', 'expiry', 'strike']).drop_duplicates()",
            "_strikes = _strikes.select('date', 'put_call', 'expiry', 'strike').sort(['date', 'put_call', 'expiry', 'strike']).unique(maintain_order=True)",
        ),
        ("date = keys.date.to_numpy()", "date = keys['date'].to_numpy()"),
        ("put_call = keys.put_call.to_numpy()", "put_call = keys['put_call'].to_numpy()"),
        ("expiry = keys.expiry.to_numpy()", "expiry = keys['expiry'].to_numpy()"),
        (
            "_values = _strikes[(_strikes.date == key[0]) & (_strikes.put_call == key[1]) & (_strikes.expiry == key[2])]\n        values = _values.strike.to_numpy().astype(int)",
            "_values = _strikes.filter((pl.col('date') == key[0]) & (pl.col('put_call') == key[1]) & (pl.col('expiry') == key[2]))\n        values = _values['strike'].to_numpy().astype(int)",
        ),
        ("for symbol in np.unique(prices.symbol.to_numpy()):", "for symbol in prices['symbol'].unique().to_list():"),
        (
            "sym_prc = prices[['timestamp', field_name]][prices.symbol == symbol].sort('timestamp')",
            "sym_prc = prices.filter(pl.col('symbol') == symbol).select('timestamp', field_name).sort('timestamp')",
        ),
        ("sym_prc.timestamp.to_numpy()", "sym_prc['timestamp'].to_numpy()"),
        (
            "spx_prices = prices[['timestamp', 'umid']].sort(['timestamp']).drop_duplicates(subset=['timestamp'])",
            "spx_prices = prices.select('timestamp', 'umid').sort('timestamp').unique(subset=['timestamp'], keep='first', maintain_order=True)",
        ),
        ("spx_prices.timestamp.to_numpy()", "spx_prices['timestamp'].to_numpy()"),
        ("spx_prices.umid.to_numpy()", "spx_prices['umid'].to_numpy()"),
        (
            "prices = pd.read_csv(filename, parse_dates=['timestamp'])\n    prices = prices[['timestamp', 'symbol', 'umid', 'c', 'delta']]",
            "prices = pl.read_csv(filename, try_parse_dates=True).select('timestamp', 'symbol', 'umid', 'c', 'delta')",
        ),
        (
            "prices['date'] = prices.timestamp.to_numpy().astype('M8[D]')",
            "prices = prices.with_columns(pl.col('timestamp').dt.date().alias('date'))",
        ),
        (
            "prices = prices[(minute > 9 * 60 + 30) & (minute < 16 * 60)]",
            "prices = prices.filter((minute > 9 * 60 + 30) & (minute < 16 * 60))",
        ),
        (
            "splits = prices.symbol.str.split('-', n=2, expand=True)\n    prices['put_call'] = splits[0]\n    prices['strike'] = splits[1].astype(int)\n    prices['expiry'] = pd.to_datetime(splits[2]).to_numpy().astype('M8[D]')",
            "prices = prices.with_columns(pl.col('symbol').str.splitn('-', 3).alias('parts')).unnest('parts').rename({'field_0': 'put_call', 'field_1': 'strike_text', 'field_2': 'expiry_text'}).with_columns(pl.col('strike_text').cast(pl.Int64).alias('strike'), pl.col('expiry_text').str.to_date('%Y-%m-%d').alias('expiry')).drop('strike_text', 'expiry_text')",
        ),
        (
            "data = prices[['timestamp', 'umid']].sort(['timestamp']).drop_duplicates(subset=['timestamp'])\n    data['date'] = data.timestamp.to_numpy().astype('M8[D]')\n    data['hour'] = data.timestamp.dt.hour",
            "data = prices.select('timestamp', 'umid').sort('timestamp').unique(subset=['timestamp'], keep='first', maintain_order=True).with_columns(pl.col('timestamp').dt.date().alias('date'), pl.col('timestamp').dt.hour().alias('hour'))",
        ),
        (
            "bod = pd.Series(np.where(data.date != data.date.shift(1), 1, np.nan))\n    bod = bod.ffill(limit=6)\n    data['bod'] = np.where(bod == 1, True, False)",
            "data = data.with_columns((pl.col('date') != pl.col('date').shift(1)).fill_null(True).alias('bod_start')).with_columns(pl.col('bod_start').cast(pl.Int8).replace(0, None).forward_fill(limit=6).fill_null(0).cast(pl.Boolean).alias('bod')).drop('bod_start')",
        ),
        (
            "eod = pd.Series(np.where(data.date != data.date.shift(-1), 1, np.nan))\n    # Try getting out 30 minutes before close so we have 6 bars to try and get out\n    eod = eod.bfill(limit=6)\n    data['eod'] = np.where(eod == 1, True, False)",
            "# Try getting out 30 minutes before close so we have 6 bars to try and get out\n    data = data.with_columns((pl.col('date') != pl.col('date').shift(-1)).fill_null(True).alias('eod_end')).with_columns(pl.col('eod_end').cast(pl.Int8).replace(0, None).backward_fill(limit=6).fill_null(0).cast(pl.Boolean).alias('eod')).drop('eod_end')",
        ),
        (
            "data['rehedge'] = (data.hour != data.hour.shift(1))",
            "data = data.with_columns((pl.col('hour') != pl.col('hour').shift(1)).fill_null(True).alias('rehedge'))",
        ),
        ("data.timestamp.to_numpy()", "data['timestamp'].to_numpy()"),
        ("data.umid.to_numpy()", "data['umid'].to_numpy()"),
        ("prices.timestamp.to_numpy()", "prices['timestamp'].to_numpy()"),
        ("prices.date", "prices['date']"),
    ],
)

replace(
    ROOT / "options_trading.ipynb",
    [
        (
            "keys = _strikes[['date', 'put_call', 'expiry']].sort_values(\n        by=['date', 'put_call', 'expiry']).drop_duplicates()",
            "keys = _strikes.select('date', 'put_call', 'expiry').sort(['date', 'put_call', 'expiry']).unique(maintain_order=True)",
        ),
        (
            "_strikes = _strikes[['date', 'put_call', 'expiry', 'strike']].sort_values(\n        by=['date', 'put_call', 'expiry', 'strike']).drop_duplicates()",
            "_strikes = _strikes.select('date', 'put_call', 'expiry', 'strike').sort(['date', 'put_call', 'expiry', 'strike']).unique(maintain_order=True)",
        ),
        (
            "minute = (prices.timestamp - prices['date']) / np.timedelta64(1, 'm')",
            "minute = (prices['timestamp'].to_numpy() - prices['date'].to_numpy().astype('M8[us]')) / np.timedelta64(1, 'm')",
        ),
        (
            "pl.col('expiry_text').str.to_date().alias('expiry')",
            "pl.col('expiry_text').str.to_date('%Y-%m-%d').alias('expiry')",
        ),
        ("str.split_exact('-', 2)", "str.splitn('-', 3)"),
        (
            "bod = pd.Series(np.where(data.date != data.date.shift(1), 1, np.nan))\n    bod = bod.ffill(limit=6)  \n    data['bod'] = np.where(bod == 1, True, False)",
            "data = data.with_columns((pl.col('date') != pl.col('date').shift(1)).fill_null(True).alias('bod_start')).with_columns(pl.col('bod_start').cast(pl.Int8).replace(0, None).forward_fill(limit=6).fill_null(0).cast(pl.Boolean).alias('bod')).drop('bod_start')",
        ),
        (
            "eod = pd.Series(np.where(data.date != data.date.shift(-1), 1, np.nan))\n    # Try getting out 30 minutes before close so we have 6 bars to try and get out\n    eod = eod.bfill(limit=6)  \n    data['eod'] = np.where(eod == 1, True, False)",
            "# Try getting out 30 minutes before close so we have 6 bars to try and get out\n    data = data.with_columns((pl.col('date') != pl.col('date').shift(-1)).fill_null(True).alias('eod_end')).with_columns(pl.col('eod_end').cast(pl.Int8).replace(0, None).backward_fill(limit=6).fill_null(0).cast(pl.Boolean).alias('eod')).drop('eod_end')",
        ),
    ],
)
