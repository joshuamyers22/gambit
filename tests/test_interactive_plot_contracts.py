import numpy as np
import polars as pl
import pytest

from gambit.interactive_plot import (
    InteractivePlot,
    LineConfig,
    LineGraphWithDetailDisplay,
    MeanWithCI,
    SimpleDetailTable,
    percentile_buckets,
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


def test_interactive_plot_applies_initial_selection_and_renders_once() -> None:
    rendered = []
    data = pl.DataFrame({"x": [1, 2], "y": [3.0, 4.0], "series": ["a", "a"], "year": [2018, 2019]})

    plot = InteractivePlot(
        data,
        plot_func=lambda _x, _y, lines: rendered.append(lines) or [],
        display_form_func=lambda *_args: None,
    )
    plot.create_pivot("x", "y", "series", {"year": 2019})

    assert plot.selection_widgets["year"].value == 2019
    assert len(rendered) == 1
    assert rendered[0][0][2]["year"].to_list() == [2019]


def test_interactive_plot_rejects_invalid_initial_selection() -> None:
    data = pl.DataFrame({"x": [1], "y": [3.0], "series": ["a"], "year": [2018]})
    plot = InteractivePlot(data, plot_func=lambda *_args: [], display_form_func=lambda *_args: None)

    with pytest.raises(ValueError, match="invalid initial selection for 'year': 2099"):
        plot.create_pivot("x", "y", "series", {"year": 2099})


def test_line_graph_constructs_figure_widget_in_visualization_environment() -> None:
    summary = pl.DataFrame({"x": [1], "y": [2.0]})
    detail = pl.DataFrame({"x": [1], "y": [2.0]})

    widgets = LineGraphWithDetailDisplay()("x", "y", [("series", summary, detail)])

    assert len(widgets) == 2


def test_line_graph_cycles_palette_for_more_than_ten_series() -> None:
    lines = []
    for index in range(11):
        summary = pl.DataFrame({"x": [1], "y": [float(index)]})
        detail = pl.DataFrame({"x": [1], "y": [float(index)]})
        lines.append((str(index), summary, detail))

    figure, _ = LineGraphWithDetailDisplay()("x", "y", lines)

    assert len(figure.data) == 11
    assert figure.data[10].line.color == figure.data[0].line.color


@pytest.mark.parametrize("color", ["#ff0000", "red", "hsl(120, 100%, 50%)"])
def test_confidence_band_accepts_plotly_color_syntax(color) -> None:
    summary = pl.DataFrame({"x": [1], "y": [2.0], "lower": [1.0], "upper": [3.0]})
    detail = pl.DataFrame({"x": [1], "y": [2.0]})
    renderer = LineGraphWithDetailDisplay(line_configs={"series": LineConfig(color=color)})

    figure, _ = renderer("x", "y", [("series", summary, detail)])

    assert figure.data[1].fillcolor == color
    assert figure.data[1].opacity == 0.3


def test_line_config_applies_requested_thickness() -> None:
    summary = pl.DataFrame({"x": [1], "y": [2.0]})
    detail = pl.DataFrame({"x": [1], "y": [2.0]})
    renderer = LineGraphWithDetailDisplay(line_configs={"series": LineConfig(thickness=4.5)})

    figure, _ = renderer("x", "y", [("series", summary, detail)])

    assert figure.data[0].line.width == 4.5


@pytest.mark.parametrize("thickness", [0, -1, np.inf, True, "2"])
def test_line_config_rejects_invalid_thickness(thickness) -> None:
    with pytest.raises(ValueError, match="line thickness"):
        LineConfig(thickness=thickness)


def test_line_graph_rejects_duplicate_series_before_detail_is_overwritten() -> None:
    summary = pl.DataFrame({"x": [1], "y": [2.0]})
    first_detail = pl.DataFrame({"x": [1], "source": ["first"]})
    second_detail = pl.DataFrame({"x": [1], "source": ["second"]})

    with pytest.raises(ValueError, match="duplicate interactive plot series"):
        LineGraphWithDetailDisplay()(
            "x",
            "y",
            [("same", summary, first_detail), ("same", summary, second_detail)],
        )


@pytest.mark.parametrize(
    ("summary", "detail", "message"),
    [
        (pl.DataFrame({"x": [1]}), pl.DataFrame({"x": [1]}), "exactly x/y"),
        (pl.DataFrame({"x": [1], "y": [2], "extra": [3]}), pl.DataFrame({"x": [1]}), "exactly x/y"),
        (pl.DataFrame({"x": [1], "y": [2]}), pl.DataFrame({"other": [1]}), "missing x column"),
    ],
)
def test_line_graph_rejects_malformed_summary_or_detail(summary, detail, message) -> None:
    with pytest.raises(ValueError, match=message):
        LineGraphWithDetailDisplay()("x", "y", [("series", summary, detail)])


def test_line_graph_requires_a_shared_x_column() -> None:
    first = ("first", pl.DataFrame({"x": [1], "y": [2]}), pl.DataFrame({"x": [1]}))
    second = ("second", pl.DataFrame({"other_x": [1], "y": [2]}), pl.DataFrame({"other_x": [1]}))

    with pytest.raises(ValueError, match="share the same x column"):
        LineGraphWithDetailDisplay()("x", "y", [first, second])


def test_line_graph_requires_detail_rows_for_every_summary_x_value() -> None:
    summary = pl.DataFrame({"x": [1, 2], "y": [3.0, 4.0]})
    detail = pl.DataFrame({"x": [1], "observation": [3.0]})

    with pytest.raises(ValueError, match="no rows for one or more 'x' values"):
        LineGraphWithDetailDisplay()("x", "y", [("series", summary, detail)])


def test_mean_with_ci_keeps_lower_and_upper_bounds_in_order(monkeypatch) -> None:
    captured = {}

    def fake_bootstrap(*_args, **kwargs):
        captured.update(kwargs)
        return 1.0, 9.0

    monkeypatch.setattr("gambit.interactive_plot.bootstrap_ci", fake_bootstrap)
    data = pl.DataFrame({"x": [1, 1], "y": [4.0, 6.0], "series": ["a", "a"]})
    statistic = MeanWithCI(mean_func=np.median, ci_level=95)

    _, summary, _ = statistic(data, "x", "y", "series")[0]

    assert summary["ci_d_95"].to_list() == [1.0]
    assert summary["ci_u_95"].to_list() == [9.0]
    assert captured["func"] is np.median


@pytest.mark.parametrize("ci_level", [-1, 100, True])
def test_mean_with_ci_rejects_invalid_confidence_levels(ci_level) -> None:
    with pytest.raises(ValueError, match="ci_level"):
        MeanWithCI(ci_level=ci_level)


def test_percentile_buckets_preserve_missing_observations() -> None:
    result = percentile_buckets(np.array([1.0, np.nan, 3.0]), n=2)

    np.testing.assert_allclose(result, np.array([1.0, np.nan, 3.0]), equal_nan=True)
    np.testing.assert_array_equal(percentile_buckets(np.array([np.nan, np.inf]), n=2), [np.nan, np.nan])


@pytest.mark.parametrize("bucket_count", [0, -1, True])
def test_percentile_buckets_reject_invalid_bucket_counts(bucket_count) -> None:
    with pytest.raises(ValueError, match="bucket count"):
        percentile_buckets(np.array([1.0]), n=bucket_count)


def test_percentile_buckets_reject_multidimensional_input() -> None:
    with pytest.raises(ValueError, match="one-dimensional"):
        percentile_buckets(np.array([[1.0, 2.0]]))
