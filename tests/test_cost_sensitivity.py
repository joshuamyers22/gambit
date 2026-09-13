from __future__ import annotations

import json

import numpy as np
import polars as pl
import pytest

from gambit.cost_sensitivity import (
    CostSensitivityCase,
    CostSensitivityEvaluationError,
    CostSensitivityResult,
    CostSensitivityRunner,
    CostSensitivityVariant,
)

pytestmark = pytest.mark.acceptance

INPUT_SHA256 = "a" * 64
STRATEGY_SHA256 = "b" * 64


def _case(
    comparison_id: str,
    variant: CostSensitivityVariant = CostSensitivityVariant.UNBUFFERED,
    *,
    spread: float = 0.01,
) -> CostSensitivityCase:
    return CostSensitivityCase(
        comparison_id,
        variant,
        {
            "slippage_model": "BidAskSpreadSlippage",
            "spread": spread,
            "commission_per_unit": 0.005,
            "participation_limit": 0.10,
            "buffer_width": 0.0 if variant is CostSensitivityVariant.UNBUFFERED else 0.05,
        },
        seed=7,
    )


def _evaluate(case: CostSensitivityCase) -> dict[str, float]:
    buffered = case.variant is CostSensitivityVariant.BUFFERED
    spread = float(case.assumptions["spread"])
    return {
        "ending_equity": 1_000.0 + spread * 100 + (2.0 if buffered else 0.0),
        "trade_count": 4.0 if buffered else 10.0,
        "turnover": 0.4 if buffered else 1.0,
    }


def test_cases_are_immutable_canonical_and_fingerprinted() -> None:
    source = {"spread": np.float32(0.01), "seeded": True, "note": "baseline"}
    case = CostSensitivityCase(
        "spread-1bp",
        CostSensitivityVariant.UNBUFFERED,
        source,
        seed=np.int64(3),
    )
    source["spread"] = 99.0

    assert dict(case.assumptions) == {"note": "baseline", "seeded": True, "spread": pytest.approx(0.01)}
    assert case.assumptions_json == '{"note":"baseline","seeded":true,"spread":0.009999999776482582}'
    assert len(case.case_sha256) == 64
    with pytest.raises(TypeError):
        case.assumptions["spread"] = 2.0  # type: ignore[index]


def test_runner_is_deterministic_and_retains_non_monotonic_path_outcomes() -> None:
    cases = (
        _case("spread-1bp", spread=0.01),
        _case("spread-2bp", spread=0.02),
    )
    runner = CostSensitivityRunner(INPUT_SHA256.upper(), STRATEGY_SHA256, cases)

    first = runner.run(_evaluate)
    second = runner.run(_evaluate)

    assert first.data.equals(second.data)
    assert first.input_sha256 == INPUT_SHA256
    assert first.data["case_index"].to_list() == [0, 0, 0, 1, 1, 1]
    assert first.data["metric"].to_list() == [
        "ending_equity",
        "trade_count",
        "turnover",
        "ending_equity",
        "trade_count",
        "turnover",
    ]
    # Higher modeled cost can change fills or decisions, so the runner records
    # the independently evaluated path and never imposes monotonicity.
    ending_equity = first.data.filter(pl.col("metric") == "ending_equity")["value"].to_list()
    assert ending_equity == [1_001.0, 1_002.0]
    assert json.loads(first.data[0, "assumptions_json"])["participation_limit"] == 0.1

    detached = first.data
    detached[0, "value"] = -1.0
    assert first.data[0, "value"] != -1.0


def test_buffer_comparison_pairs_only_matching_complete_variants() -> None:
    runner = CostSensitivityRunner(
        INPUT_SHA256,
        STRATEGY_SHA256,
        (
            _case("spread-1bp"),
            _case("spread-1bp", CostSensitivityVariant.BUFFERED),
            _case("unpaired", spread=0.02),
        ),
    )

    result = runner.run(_evaluate)
    comparison = result.compare_buffering("trade_count")

    assert comparison.select(
        "comparison_id", "metric", "unbuffered_value", "buffered_value", "difference"
    ).row(0) == ("spread-1bp", "trade_count", 10.0, 4.0, -6.0)
    assert len(comparison[0, "unbuffered_case_sha256"]) == 64
    assert result.compare_buffering("ending_equity")[0, "difference"] == 2.0
    with pytest.raises(ValueError, match="unavailable"):
        result.compare_buffering("unknown")


@pytest.mark.parametrize(
    "assumptions",
    [
        {},
        {"": 1.0},
        {"spread": np.nan},
        {"parameters": [1.0]},
        {"model": ""},
    ],
)
def test_cases_reject_ambiguous_assumptions(assumptions: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        CostSensitivityCase(
            "invalid",
            CostSensitivityVariant.UNBUFFERED,
            assumptions,  # type: ignore[arg-type]
        )


def test_runner_rejects_invalid_identity_and_case_sets() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        CostSensitivityRunner("bad", STRATEGY_SHA256, [_case("one")])
    with pytest.raises(ValueError, match="cannot be empty"):
        CostSensitivityRunner(INPUT_SHA256, STRATEGY_SHA256, [])
    with pytest.raises(ValueError, match="must be unique"):
        CostSensitivityRunner(INPUT_SHA256, STRATEGY_SHA256, [_case("one"), _case("one")])


def test_runner_rejects_failed_or_incomparable_evaluations() -> None:
    runner = CostSensitivityRunner(INPUT_SHA256, STRATEGY_SHA256, [_case("one"), _case("two")])

    with pytest.raises(CostSensitivityEvaluationError, match="one:unbuffered"):
        runner.run(lambda _case: 1 / 0)
    with pytest.raises(ValueError, match="same metric names"):
        runner.run(lambda case: {"a": 1.0} if case.comparison_id == "one" else {"b": 1.0})
    with pytest.raises(ValueError, match="must be finite"):
        runner.run(lambda _case: {"metric": np.inf})
    with pytest.raises(TypeError, match="real number"):
        runner.run(lambda _case: {"metric": True})


def test_result_constructor_revalidates_fingerprints() -> None:
    result = CostSensitivityRunner(INPUT_SHA256, STRATEGY_SHA256, [_case("one")]).run(_evaluate)
    corrupted = result.data.with_columns(pl.lit("0" * 64).alias("case_sha256"))

    with pytest.raises(ValueError, match="case fingerprint"):
        CostSensitivityResult(INPUT_SHA256, STRATEGY_SHA256, corrupted)
