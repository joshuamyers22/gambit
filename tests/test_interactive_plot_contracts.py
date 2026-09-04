import polars as pl

from gambit.interactive_plot import (
    InteractivePlot,
    LineConfig,
    LineGraphWithDetailDisplay,
    MeanWithCI,
    SimpleDetailTable,
)


def test_detail_table_respects_copy_to_clipboard_option() -> None:
    table = SimpleDetailTable(copy_to_clipboard=False)

    assert table.copy_to_clipboard is False


def test_line_graph_default_configuration_is_not_shared() -> None:
    first = LineGraphWithDetailDisplay()
    second = LineGraphWithDetailDisplay()

    first.line_configs["first"] = LineConfig()

    assert second.line_configs == {}


def test_interactive_plot_default_services_are_not_shared() -> None:
    data = pl.DataFrame({"x": [1], "y": [2], "series": ["a"]})

    first = InteractivePlot(data)
    second = InteractivePlot(data)

    assert first.transform_func is not second.transform_func
    assert first.stat_func is not second.stat_func
    assert first.plot_func is not second.plot_func


def test_mean_with_ci_keeps_lower_and_upper_bounds_in_order(monkeypatch) -> None:
    monkeypatch.setattr("gambit.interactive_plot.bootstrap_ci", lambda *_args, **_kwargs: (1.0, 9.0))
    data = pl.DataFrame({"x": [1, 1], "y": [4.0, 6.0], "series": ["a", "a"]})

    _, summary, _ = MeanWithCI(ci_level=95)(data, "x", "y", "series")[0]

    assert summary["ci_d_95"].to_list() == [1.0]
    assert summary["ci_u_95"].to_list() == [9.0]
