import subprocess
import sys

import numpy as np
import polars as pl
import pytest

from gambit.account import Account
from gambit.evaluator import compute_return_metrics
from gambit.market_data import validate_market_data
from gambit.pq_types import ContractGroup
from gambit.strategy_builder import StrategyBuilder


def test_root_import_does_not_load_optional_dependencies() -> None:
    blocked = ["h5py", "IPython", "ipywidgets", "pandas_market_calendars", "plotly", "scipy", "statsmodels", "traitlets"]
    script = f"""
import builtins
blocked = {blocked!r}
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if any(name == item or name.startswith(item + '.') for item in blocked):
        raise ImportError('blocked optional dependency: ' + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import gambit
assert gambit.ContractGroupSpec
timestamps = __import__('numpy').array(['2026-01-02'], dtype='datetime64[D]')
group = gambit.ContractGroup.get('minimal')
gambit.Contract.create('MINIMAL', group)
strategy = gambit.Strategy(timestamps, [group], lambda *_args: 100.0)
result = strategy.run()
assert result.trades.is_empty()
"""

    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("starting_equity", [0.0, -1.0, np.inf, np.nan])
def test_account_rejects_non_positive_or_non_finite_starting_equity(starting_equity: float) -> None:
    timestamp = np.array(["2026-01-01"], dtype="datetime64[D]")

    with pytest.raises(ValueError, match="finite and positive"):
        Account([ContractGroup.get("equity")], timestamp, lambda *_args: 1.0, None, starting_equity)


def test_builder_rejects_zero_starting_equity_immediately() -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        StrategyBuilder().set_starting_equity(0.0)


def test_return_metrics_reject_empty_series() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        compute_return_metrics(
            np.array([], dtype="datetime64[ns]"),
            np.array([], dtype=np.float64),
            1_000.0,
        )


def test_market_data_reports_typed_empty_frame() -> None:
    data = pl.DataFrame(schema={"timestamp": pl.Datetime("ns"), "price": pl.Float64})

    report = validate_market_data(data)

    assert not report.is_valid
    assert report.by_code("empty_data").count == 0
