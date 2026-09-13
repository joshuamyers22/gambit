"""Run a fingerprinted cost/participation and buffering sensitivity surface."""

import hashlib

from gambit.cost_sensitivity import CostSensitivityCase, CostSensitivityRunner, CostSensitivityVariant


def sha256(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


cases = [
    CostSensitivityCase(
        comparison_id=f"spread-{spread:.2f}",
        variant=variant,
        assumptions={
            "slippage_model": "BidAskSpreadSlippage",
            "spread": spread,
            "commission_per_unit": 0.005,
            "participation_limit": 0.10,
            "buffer_width": 0.0 if variant is CostSensitivityVariant.UNBUFFERED else 0.05,
        },
        seed=42,
    )
    for spread in (0.00, 0.02, 0.05)
    for variant in (CostSensitivityVariant.UNBUFFERED, CostSensitivityVariant.BUFFERED)
]


def evaluate(case: CostSensitivityCase) -> dict[str, float]:
    """Replace this controlled fixture with one complete independent strategy run."""
    buffered = case.variant is CostSensitivityVariant.BUFFERED
    trade_count = 4.0 if buffered else 12.0
    turnover = 0.4 if buffered else 1.2
    gross_pnl = 110.0 if buffered else 100.0
    explicit_cost = trade_count * float(case.assumptions["commission_per_unit"])
    price_effect = trade_count * float(case.assumptions["spread"]) / 2
    return {
        "ending_equity": 10_000.0 + gross_pnl - explicit_cost - price_effect,
        "gross_pnl": gross_pnl,
        "net_pnl": gross_pnl - explicit_cost - price_effect,
        "trade_count": trade_count,
        "turnover": turnover,
    }


result = CostSensitivityRunner(
    input_sha256=sha256("controlled-cost-input-v1"),
    strategy_sha256=sha256("controlled-cost-strategy-v1"),
    cases=cases,
).run(evaluate)

assert result.data.select("comparison_id", "variant").unique().height == 6
assert result.compare_buffering("trade_count")[0, "difference"] == -8.0
print(result.data)
print(result.compare_buffering("net_pnl"))
