from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from gambit.cost_diagnostics import CostPeriod, CostTurnoverAnalyzer

pytestmark = pytest.mark.acceptance


def _pnl(*, net: tuple[float, ...]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": np.array(["2026-01-02", "2026-01-03", "2026-02-02"], dtype="datetime64[ns]"),
            "symbol": ["A", "A", "A"],
            "rule": ["carry", "carry", "carry"],
            "gross_pnl": [10.0, -4.0, 5.0],
            "net_pnl": net,
        }
    )


def _trades(*, fee: tuple[float, ...], commission: tuple[float, ...]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": np.array(["2026-01-02", "2026-01-03", "2026-02-02"], dtype="datetime64[ns]"),
            "symbol": ["A", "A", "A"],
            "rule": ["carry", "carry", "carry"],
            "qty": [2.0, -2.0, 1.0],
            "price": [100.0, 110.0, 120.0],
            "multiplier": [10.0, 10.0, 10.0],
            "fee": fee,
            "commission": commission,
        }
    )


def test_zero_cost_fixture_has_matching_gross_and_net_results() -> None:
    report = CostTurnoverAnalyzer(10_000.0).analyze(
        _pnl(net=(10.0, -4.0, 5.0)),
        _trades(fee=(0.0, 0.0, 0.0), commission=(0.0, 0.0, 0.0)),
    ).data

    assert report.select("gross_pnl", "net_pnl", "explicit_cost", "explicit_cost_drag").rows() == [
        (6.0, 6.0, 0.0, 0.0),
        (5.0, 5.0, 0.0, 0.0),
    ]
    assert report[0, "traded_notional"] == 4_200.0
    assert report[0, "turnover"] == pytest.approx(0.42)
    assert report.select("period", "capital").unique().row(0) == ("monthly", 10_000.0)


def test_high_turnover_fixture_reconciles_visible_cost_drag() -> None:
    report = CostTurnoverAnalyzer(10_000.0).analyze(
        _pnl(net=(8.5, -5.5, 3.5)),
        _trades(fee=(0.5, 0.5, 0.5), commission=(1.0, 1.0, 1.0)),
    ).data

    assert report.select("gross_pnl", "net_pnl", "fee", "commission", "explicit_cost").rows() == [
        (6.0, 3.0, 1.0, 2.0, 3.0),
        (5.0, 3.5, 0.5, 1.0, 1.5),
    ]
    assert report[0, "gross_return"] == pytest.approx(0.0006)
    assert report[0, "net_return"] == pytest.approx(0.0003)
    assert report[0, "explicit_cost_drag"] == pytest.approx(0.0003)


def test_period_instrument_and_rule_dimensions_remain_separate() -> None:
    pnl = pl.DataFrame(
        {
            "timestamp": np.array(["2026-01-02", "2026-01-02"], dtype="datetime64[ns]"),
            "symbol": ["A", "B"],
            "rule": ["carry", "momentum"],
            "gross_pnl": [3.0, 4.0],
            "net_pnl": [2.0, 2.0],
        }
    )
    trades = pl.DataFrame(
        {
            "timestamp": np.array(["2026-01-02", "2026-01-02"], dtype="datetime64[ns]"),
            "symbol": ["A", "B"],
            "rule": ["carry", "momentum"],
            "qty": [1.0, -2.0],
            "price": [100.0, 50.0],
            "multiplier": [1.0, 10.0],
            "fee": [0.25, 0.5],
            "commission": [0.75, 1.5],
        }
    )

    report = CostTurnoverAnalyzer(1_000.0, period=CostPeriod.DAILY).analyze(pnl, trades).data

    assert report.select("symbol", "rule", "traded_notional", "turnover").rows() == [
        ("A", "carry", 100.0, 0.1),
        ("B", "momentum", 1_000.0, 1.0),
    ]


def test_diagnostics_reject_unreconciled_or_ambiguous_inputs() -> None:
    analyzer = CostTurnoverAnalyzer(10_000.0)
    with pytest.raises(ValueError, match="does not reconcile"):
        analyzer.analyze(
            _pnl(net=(10.0, -4.0, 5.0)),
            _trades(fee=(1.0, 0.0, 0.0), commission=(0.0, 0.0, 0.0)),
        )
    with pytest.raises(ValueError, match="nonzero quantity"):
        analyzer.analyze(
            _pnl(net=(10.0, -4.0, 5.0)),
            _trades(fee=(0.0, 0.0, 0.0), commission=(0.0, 0.0, 0.0)).with_columns(
                pl.lit(0.0).alias("qty")
            ),
        )
    with pytest.raises(ValueError, match="finite and positive"):
        CostTurnoverAnalyzer(0.0)
    with pytest.raises(TypeError, match="numeric non-boolean"):
        analyzer.analyze(
            _pnl(net=(10.0, -4.0, 5.0)).with_columns(pl.lit(True).alias("gross_pnl")),
            _trades(fee=(0.0, 0.0, 0.0), commission=(0.0, 0.0, 0.0)),
        )


def test_report_data_is_detached() -> None:
    result = CostTurnoverAnalyzer(10_000.0).analyze(
        _pnl(net=(10.0, -4.0, 5.0)),
        _trades(fee=(0.0, 0.0, 0.0), commission=(0.0, 0.0, 0.0)),
    )
    data = result.data
    data[0, "gross_pnl"] = 999.0

    assert result.data[0, "gross_pnl"] != 999.0
