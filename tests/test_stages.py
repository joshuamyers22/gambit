
import numpy as np
import pytest

from gambit.pq_types import ContractGroup
from gambit.stages import ExecutionStage, IndicatorStage, StageGraph, StageNode
from gambit.strategy import Strategy
from gambit.strategy_components import SimpleMarketSimulator, VectorIndicator


def _price(_contract, _timestamps, _index, _context):
    return 100.0


def test_builtin_components_implement_stage_protocols() -> None:
    assert isinstance(VectorIndicator(np.array([1.0])), IndicatorStage)
    assert isinstance(SimpleMarketSimulator(_price), ExecutionStage)


def test_stage_graph_orders_dependencies_before_consumers() -> None:
    graph = StageGraph(
        [
            StageNode("rule:entry", "rule", ("signal:entry",)),
            StageNode("signal:entry", "signal", ("indicator:price",)),
            StageNode("indicator:price", "indicator"),
        ]
    )

    assert graph.topological_order() == ("indicator:price", "signal:entry", "rule:entry")


def test_stage_graph_reports_missing_dependencies() -> None:
    graph = StageGraph([StageNode("signal:entry", "signal", ("indicator:missing",))])

    with pytest.raises(ValueError, match="indicator:missing"):
        graph.topological_order()


def test_stage_graph_reports_dependency_cycle() -> None:
    graph = StageGraph(
        [
            StageNode("indicator:first", "indicator", ("indicator:second",)),
            StageNode("indicator:second", "indicator", ("indicator:first",)),
        ]
    )

    with pytest.raises(ValueError, match="indicator:first -> indicator:second -> indicator:first"):
        graph.topological_order()


def test_strategy_stage_graph_rejects_indicator_dependency_scope_gap() -> None:
    first_group = ContractGroup.get("indicator-parent-scope")
    second_group = ContractGroup.get("indicator-child-scope")
    strategy = Strategy(np.array([np.datetime64("2024-01-02")]), [first_group, second_group], _price)
    strategy.add_indicator("parent", lambda *_args: np.array([1.0]), [first_group])
    strategy.add_indicator("child", lambda *_args: np.array([2.0]), [second_group], depends_on=["parent"])

    with pytest.raises(ValueError, match="indicator 'parent'.*indicator-child-scope"):
        strategy.validate_stage_graph()


def test_strategy_stage_graph_rejects_signal_dependency_scope_gap() -> None:
    first_group = ContractGroup.get("signal-parent-scope")
    second_group = ContractGroup.get("signal-child-scope")
    strategy = Strategy(np.array([np.datetime64("2024-01-02")]), [first_group, second_group], _price)
    strategy.add_signal("parent", lambda *_args: np.array([True]), [first_group])
    strategy.add_signal("child", lambda *_args: np.array([True]), [second_group], depends_on_signals=["parent"])

    with pytest.raises(ValueError, match="signal 'parent'.*signal-child-scope"):
        strategy.validate_stage_graph()


def test_strategy_exposes_complete_stage_metadata() -> None:
    group = ContractGroup.get("stages")
    timestamp = np.array([np.datetime64("2024-01-02")])
    strategy = Strategy(timestamp, [group], _price)
    strategy.add_indicator("price", lambda *_args: np.array([100.0]))
    strategy.add_signal("entry", lambda *_args: np.array([True]), depends_on_indicators=["price"])
    strategy.add_rule("entry", lambda *_args: [], "entry")
    strategy.add_market_sim(SimpleMarketSimulator(_price))

    assert strategy.validate_stage_graph() == (
        "indicator:price",
        "signal:entry",
        "rule:entry",
        "execution:0",
        "accounting:main",
    )
    assert strategy.stage_graph().describe()[0] == {
        "name": "indicator:price",
        "kind": "indicator",
        "dependencies": [],
    }
