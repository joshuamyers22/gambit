# P1.7 executable-target signoff

Status: **awaiting quant/execution-owner approval**

Tested implementation baseline: `862e693` on `production-io-admission`.
The approval record must identify the immutable commit actually reviewed.

## Scope

P1.7 converts base-currency exposure targets into whole-contract incremental
orders, applies deterministic no-trade buffering, preserves required constraint
reductions, recalculates achieved risk, and connects the result to `Strategy`
through its existing validation and admission boundary.

Live brokerage connectivity, production order routing, cost-aware optimization,
and claims that a configured risk model predicts future outcomes are not part of
this signoff.

## Acceptance evidence

| Requirement | Evidence |
|---|---|
| Price, multiplier, and FX conversion reconciles to whole units | `tests/test_target_positions.py::test_targets_reconcile_fx_multiplier_holdings_pending_and_lots` |
| Rounding is deterministic, including signed ties and lots | `tests/test_target_positions.py::test_rounding_is_deterministic_for_sign_ties_and_lots` |
| Small/impossible targets and invalid prices fail or retain explicit tracking error | `test_small_target_rounds_to_zero_with_visible_tracking_error`, `test_targets_reject_nonpositive_or_nonfinite_prices` |
| Pending and cancellation-requested quantities prevent duplicate proposals | `test_repeated_target_accounts_for_every_still_open_order` |
| Partial fills reserve the account fill plus only the remaining order quantity | `tests/test_position_fill_sequences.py::test_partial_fill_uses_account_position_and_only_remaining_quantity` |
| Buffering reduces controlled oscillation | `test_no_trade_band_reduces_controlled_target_oscillation` |
| Required long-only and position-limit reductions bypass buffering, not admission | `test_no_trade_band_is_overridden_for_required_constraint_reductions` |
| Rejected proposals do not enter achieved exposure or risk | `test_target_admission_excludes_rejections_from_orders_and_achieved_risk` |
| Achieved risk uses actual rounded/buffered/admitted quantities | `test_achieved_exposures_and_risk_follow_rounding_and_buffering` |
| Strategy adapter reserves live pending orders and uses final re-admission | `test_executable_target_rule_uses_strategy_pending_and_readmission_path` and `examples/risk/executable_target_strategy.py` |
| Point-in-time market-data and model cutoffs remain enforced | `test_targets_reject_lookahead_prices_and_fx`, `test_achieved_risk_rejects_future_models_and_invalid_measure_collections` |

The full gate at the tested baseline passed 2,094 tests at 86% aggregate
coverage, including 86% target-module and 94% risk-module coverage; all six
enforced module floors; 10/10 financial mutations; frozen lock; Ruff; mypy;
native-warning and notebook-cleanliness checks; strict Sphinx; wheel and sdist;
Twine; and release-artifact verification.

## Operational decisions to review

- Target and price rows are exact by symbol; prices must be positive, finite,
  correctly currency-labelled, and no newer than the calculation cutoff.
- Whole-lot nearest rounding moves ties away from zero. Toward-zero rounding is
  available only when explicitly configured.
- A no-trade band is an inclusive, symmetric absolute amount in calculation base
  currency around the rounded target.
- Current holdings and every still-open order, including cancellation requests,
  are reserved. Filled and acknowledged-cancelled orders release reservation.
- `LongOnly` and `MaxPositionQuantity` can identify an existing-breach reduction
  that must bypass buffering. The resulting order still passes every policy.
- Target construction admits proposals sequentially. Rejections remain visible
  in detached decisions and diagnostics but are absent from returned orders and
  achieved risk.
- `ExecutableTargetRule` must be configured with policies equivalent to those
  registered on `Strategy`. Strategy performs the authoritative final admission
  against then-current state.
- Results and decisions are diagnostic evidence, not proof of future model
  accuracy and not a live-routing authorization.

## Reviewer procedure

1. Review the implementation and public contracts at the immutable review SHA.
2. Run `make check` on the supported review host and retain the complete output.
3. Run `python examples/risk/executable_target_strategy.py` and inspect the
   pending-order/no-duplicate evidence.
4. Confirm the operational decisions above match the intended portfolio and
   execution policy.
5. Record the decision below. A conditional or rejected decision must identify
   concrete follow-up work and does not close the production gate.

## Approval record

- Reviewed commit:
- Reviewer name and role:
- Review date:
- Decision: `approved` / `conditional` / `rejected`
- Conditions or findings:
- Evidence location:

Repository automation and an AI assistant must not populate these fields or
represent owner approval. The named quant/execution owner records the decision.
