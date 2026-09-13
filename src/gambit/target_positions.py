"""Convert base-currency exposure targets into auditable executable quantities."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from numbers import Integral, Real
from types import MappingProxyType
from typing import Any, cast

import numpy as np
import polars as pl

from gambit.account import Account
from gambit.boundaries import timestamp_index
from gambit.calculation import CalculationContext
from gambit.currency import FxRateSnapshot
from gambit.execution_snapshots import snapshot_order
from gambit.pq_types import Contract, ContractGroup, MarketOrder, Order, _validate_order_references, _whole_quantity
from gambit.risk import (
    DecisionStatus,
    OrderDecision,
    RiskContext,
    RiskPolicy,
    decide_order,
    requires_risk_reduction,
)
from gambit.risk_measures import RiskMeasure, RiskResult, calculate_risk


class TargetRounding(str, Enum):
    """Deterministic conversion from continuous targets to tradable lots."""

    NEAREST = "nearest"
    TOWARD_ZERO = "toward_zero"


@dataclass(frozen=True)
class TradableUnitRule:
    """Whole-contract rounding and base-currency no-trade band for one instrument."""

    lot_size: int = 1
    rounding: TargetRounding = TargetRounding.NEAREST
    no_trade_band: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.lot_size, bool) or not isinstance(self.lot_size, Integral):
            raise TypeError("tradable lot_size must be an integer")
        lot_size = int(self.lot_size)
        if not 0 < lot_size <= np.iinfo(np.int_).max:
            raise ValueError("tradable lot_size must be a positive platform integer")
        if not isinstance(self.rounding, TargetRounding):
            raise TypeError("tradable rounding must be TargetRounding")
        if isinstance(self.no_trade_band, bool) or not isinstance(self.no_trade_band, Real):
            raise TypeError("tradable no_trade_band must be a real number")
        no_trade_band = float(self.no_trade_band)
        if not math.isfinite(no_trade_band) or no_trade_band < 0:
            raise ValueError("tradable no_trade_band must be finite and non-negative")
        object.__setattr__(self, "lot_size", lot_size)
        object.__setattr__(self, "no_trade_band", no_trade_band)

    def round(self, raw_quantity: float) -> int:
        """Round in lot units; nearest ties move away from zero."""
        if not math.isfinite(raw_quantity):
            raise ValueError("raw target quantity must be finite")
        lots = raw_quantity / self.lot_size
        if self.rounding is TargetRounding.NEAREST:
            magnitude = math.floor(abs(lots) + 0.5)
            rounded_lots = magnitude if lots >= 0 else -magnitude
        else:
            rounded_lots = math.trunc(lots)
        quantity = int(rounded_lots) * self.lot_size
        bounds = np.iinfo(np.int_)
        if not bounds.min <= quantity <= bounds.max:
            raise ValueError("target quantity must fit the signed platform integer range")
        return quantity


def _rules(values: Mapping[str, TradableUnitRule]) -> Mapping[str, TradableUnitRule]:
    if not isinstance(values, Mapping):
        raise TypeError("tradable unit rules must be a mapping")
    normalized: dict[str, TradableUnitRule] = {}
    for symbol, rule in values.items():
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("tradable unit rule symbols must be non-empty strings")
        if not isinstance(rule, TradableUnitRule):
            raise TypeError("tradable unit rule values must be TradableUnitRule objects")
        normalized[symbol] = rule
    if not normalized:
        raise ValueError("tradable unit rules cannot be empty")
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, init=False)
class ExecutableTargetResult:
    """Detached target diagnostics, achieved exposures, risk, and admitted orders."""

    _positions: pl.DataFrame
    _orders: tuple[MarketOrder, ...]
    _exposures: pl.DataFrame
    _risk: RiskResult | None
    _decisions: tuple[OrderDecision, ...]

    def __init__(
        self,
        positions: pl.DataFrame,
        orders: Sequence[MarketOrder],
        exposures: pl.DataFrame,
        risk: RiskResult | None,
        decisions: Sequence[OrderDecision],
    ) -> None:
        object.__setattr__(self, "_positions", positions.clone())
        object.__setattr__(self, "_orders", tuple(snapshot_order(order) for order in orders))
        object.__setattr__(self, "_exposures", exposures.clone())
        object.__setattr__(
            self,
            "_risk",
            None if risk is None else RiskResult(risk.data.clone()),
        )
        object.__setattr__(self, "_decisions", tuple(self._snapshot_decision(item) for item in decisions))

    @staticmethod
    def _snapshot_decision(decision: OrderDecision) -> OrderDecision:
        return OrderDecision(
            snapshot_order(decision.order),
            decision.status,
            decision.policy,
            decision.code,
            decision.message,
            decision.proposed_qty,
            decision.timestamp,
        )

    @property
    def positions(self) -> pl.DataFrame:
        return self._positions.clone()

    @property
    def orders(self) -> tuple[MarketOrder, ...]:
        return tuple(snapshot_order(order) for order in self._orders)

    @property
    def exposures(self) -> pl.DataFrame:
        """Post-order base-currency exposure rows used by risk measures."""
        return self._exposures.clone()

    @property
    def risk(self) -> RiskResult | None:
        """Post-rounding and post-buffer risk, when measures were requested."""
        return None if self._risk is None else RiskResult(self._risk.data.clone())

    @property
    def decisions(self) -> tuple[OrderDecision, ...]:
        """Detached admission decisions for every nonzero buffered proposal."""
        return tuple(self._snapshot_decision(item) for item in self._decisions)


@dataclass(frozen=True)
class ExecutableTargetInputs:
    """Point-in-time target, price, FX, and calculation inputs for a rule call."""

    exposures: pl.DataFrame
    prices: pl.DataFrame
    fx: FxRateSnapshot
    calculation: CalculationContext


@dataclass(frozen=True)
class ExecutableTargetBuilder:
    """Build whole-contract targets using current and pending positions."""

    unit_rules: Mapping[str, TradableUnitRule]
    reason_code: str = "portfolio_target"

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit_rules", _rules(self.unit_rules))
        if not isinstance(self.reason_code, str) or not self.reason_code:
            raise ValueError("target order reason_code must be a non-empty string")

    def build(
        self,
        exposures: pl.DataFrame,
        contracts: Sequence[Contract],
        prices: pl.DataFrame,
        fx: FxRateSnapshot,
        context: CalculationContext | np.datetime64,
        account: Account,
        *,
        pending_orders: Sequence[Order] = (),
        risk_measures: Sequence[RiskMeasure] = (),
        risk_policies: Sequence[RiskPolicy] = (),
    ) -> ExecutableTargetResult:
        """Return detached diagnostics, achieved risk, decisions, and admitted orders."""
        calculation = CalculationContext.coerce(context)
        if not isinstance(account, Account):
            raise TypeError("target account must be an Account")
        if not isinstance(risk_measures, Sequence) or isinstance(risk_measures, (str, bytes)):
            raise TypeError("target risk_measures must be a sequence")
        measures = tuple(risk_measures)
        policies = self._policies(risk_policies)
        timestamp_index(account.timestamps, calculation.valuation_time, owner="target builder")
        exposure_values = self._exposures(exposures, calculation)
        contract_by_symbol = self._contracts(contracts, set(exposure_values["symbol"].to_list()))
        price_by_symbol = self._prices(prices, contract_by_symbol, calculation)
        self._validate_fx(fx, calculation)
        pending = tuple(pending_orders) if isinstance(pending_orders, Sequence) else pending_orders
        pending_by_symbol = self._pending(pending, contract_by_symbol)

        rows: list[dict[str, object]] = []
        orders: list[MarketOrder] = []
        decisions: list[OrderDecision] = []
        for symbol, target_exposure in exposure_values.select("symbol", "net_exposure").iter_rows():
            contract = contract_by_symbol[symbol]
            price, price_as_of = price_by_symbol[symbol]
            local_currency = contract.instrument_spec.currency
            fx_rate = fx.rate(local_currency)
            unit_notional = price * contract.multiplier * fx_rate
            if not math.isfinite(unit_notional) or unit_notional <= 0:
                raise ValueError(f"target unit notional must be finite and positive for {symbol}")
            raw_target = target_exposure / unit_notional
            rule = self.unit_rules[symbol]
            target_quantity = rule.round(raw_target)
            current_quantity = self._position(account, contract, calculation.valuation_time)
            pending_quantity = pending_by_symbol.get(symbol, 0)
            projected_quantity = current_quantity + pending_quantity
            bounds = np.iinfo(np.int_)
            if not bounds.min <= projected_quantity <= bounds.max:
                raise ValueError(
                    f"projected target quantity is outside the platform integer range for {symbol}"
                )
            unbuffered_order_quantity = target_quantity - projected_quantity
            if not bounds.min <= unbuffered_order_quantity <= bounds.max:
                raise ValueError(f"target order quantity is outside the platform integer range for {symbol}")
            rounded_target_exposure = target_quantity * unit_notional
            projected_exposure = projected_quantity * unit_notional
            if not math.isfinite(rounded_target_exposure) or not math.isfinite(projected_exposure):
                raise ValueError(f"target exposure arithmetic must remain finite for {symbol}")
            inside_no_trade_band = (
                abs(rounded_target_exposure - projected_exposure) <= rule.no_trade_band
            )
            buffer_applied = bool(unbuffered_order_quantity and inside_no_trade_band)
            buffer_overridden_for_risk = False
            if buffer_applied:
                reduction = MarketOrder(
                    contract=contract,
                    timestamp=calculation.valuation_time,
                    qty=unbuffered_order_quantity,
                    reason_code=self.reason_code,
                )
                buffer_overridden_for_risk = requires_risk_reduction(
                    reduction,
                    RiskContext(account, calculation.valuation_time, (*pending, *orders)),
                    policies,
                )
                buffer_applied = not buffer_overridden_for_risk
            proposal_quantity = 0 if buffer_applied else unbuffered_order_quantity
            order_quantity = 0
            admission_status = "not_proposed"
            admission_policy = ""
            admission_code = "not_proposed"
            admission_message = ""
            if proposal_quantity:
                proposal = MarketOrder(
                    contract=contract,
                    timestamp=calculation.valuation_time,
                    qty=proposal_quantity,
                    reason_code=self.reason_code,
                )
                decision = decide_order(
                    proposal,
                    RiskContext(account, calculation.valuation_time, (*pending, *orders)),
                    policies,
                )
                decisions.append(decision)
                admission_status = decision.status.value
                admission_policy = decision.policy
                admission_code = decision.code
                admission_message = decision.message
                if decision.status is DecisionStatus.ACCEPTED:
                    order_quantity = proposal_quantity
                    orders.append(proposal)
            post_order_quantity = projected_quantity + order_quantity
            achieved_exposure = post_order_quantity * unit_notional
            tracking_error = achieved_exposure - target_exposure
            if not math.isfinite(achieved_exposure) or not math.isfinite(tracking_error):
                raise ValueError(f"target exposure arithmetic must remain finite for {symbol}")
            rows.append(
                {
                    "symbol": symbol,
                    "contract_group": contract.contract_group.name,
                    "asset_class": contract.instrument_spec.asset_class.value,
                    "currency": calculation.base_currency,
                    "target_net_exposure": target_exposure,
                    "local_currency": local_currency,
                    "price": price,
                    "price_as_of": price_as_of,
                    "fx_rate": fx_rate,
                    "fx_as_of": fx.as_of,
                    "multiplier": contract.multiplier,
                    "base_unit_notional": unit_notional,
                    "lot_size": rule.lot_size,
                    "rounding": rule.rounding.value,
                    "no_trade_band": rule.no_trade_band,
                    "raw_target_quantity": raw_target,
                    "target_quantity": target_quantity,
                    "rounded_target_exposure": rounded_target_exposure,
                    "current_quantity": current_quantity,
                    "pending_quantity": pending_quantity,
                    "projected_quantity": projected_quantity,
                    "projected_net_exposure": projected_exposure,
                    "unbuffered_order_quantity": unbuffered_order_quantity,
                    "inside_no_trade_band": inside_no_trade_band,
                    "buffer_applied": buffer_applied,
                    "buffer_overridden_for_risk": buffer_overridden_for_risk,
                    "proposal_quantity": proposal_quantity,
                    "admission_status": admission_status,
                    "admission_policy": admission_policy,
                    "admission_code": admission_code,
                    "admission_message": admission_message,
                    "order_quantity": order_quantity,
                    "post_order_quantity": post_order_quantity,
                    "achieved_net_exposure": achieved_exposure,
                    "tracking_error": tracking_error,
                }
            )
        positions = pl.DataFrame(
            rows,
            schema_overrides={
                "symbol": pl.String,
                "contract_group": pl.String,
                "asset_class": pl.String,
                "currency": pl.String,
                "target_net_exposure": pl.Float64,
                "local_currency": pl.String,
                "price": pl.Float64,
                "price_as_of": pl.Datetime("ns"),
                "fx_rate": pl.Float64,
                "fx_as_of": pl.Datetime("ns"),
                "multiplier": pl.Float64,
                "base_unit_notional": pl.Float64,
                "lot_size": pl.Int64,
                "rounding": pl.String,
                "no_trade_band": pl.Float64,
                "raw_target_quantity": pl.Float64,
                "target_quantity": pl.Int64,
                "rounded_target_exposure": pl.Float64,
                "current_quantity": pl.Int64,
                "pending_quantity": pl.Int64,
                "projected_quantity": pl.Int64,
                "projected_net_exposure": pl.Float64,
                "unbuffered_order_quantity": pl.Int64,
                "inside_no_trade_band": pl.Boolean,
                "buffer_applied": pl.Boolean,
                "buffer_overridden_for_risk": pl.Boolean,
                "proposal_quantity": pl.Int64,
                "admission_status": pl.String,
                "admission_policy": pl.String,
                "admission_code": pl.String,
                "admission_message": pl.String,
                "order_quantity": pl.Int64,
                "post_order_quantity": pl.Int64,
                "achieved_net_exposure": pl.Float64,
                "tracking_error": pl.Float64,
            },
        )
        achieved_exposures = positions.select(
            "symbol",
            "contract_group",
            "asset_class",
            "currency",
            (pl.col("price") * pl.col("fx_rate")).alias("price"),
            pl.col("post_order_quantity").alias("quantity"),
            "multiplier",
            pl.col("achieved_net_exposure").alias("net_exposure"),
            pl.col("achieved_net_exposure").abs().alias("gross_exposure"),
        )
        risk = calculate_risk(achieved_exposures, measures, calculation) if measures else None
        return ExecutableTargetResult(positions, orders, achieved_exposures, risk, decisions)

    @staticmethod
    def _policies(policies: Sequence[RiskPolicy]) -> tuple[RiskPolicy, ...]:
        if not isinstance(policies, Sequence) or isinstance(policies, (str, bytes)):
            raise TypeError("target risk_policies must be a sequence")
        normalized = tuple(policies)
        for policy in normalized:
            if not isinstance(getattr(policy, "name", None), str) or not policy.name:
                raise TypeError("target risk policies must expose a non-empty name")
            if not callable(getattr(policy, "evaluate", None)):
                raise TypeError("target risk policies must expose evaluate")
        return normalized

    def _exposures(self, exposures: pl.DataFrame, context: CalculationContext) -> pl.DataFrame:
        if not isinstance(exposures, pl.DataFrame):
            raise TypeError("target exposures must be a Polars DataFrame")
        required = {"symbol", "currency", "net_exposure"}
        if missing := required - set(exposures.columns):
            raise ValueError(f"target exposure columns are missing: {', '.join(sorted(missing))}")
        if exposures.is_empty():
            raise ValueError("target exposures cannot be empty")
        if not exposures.schema["net_exposure"].is_numeric() or exposures.schema["net_exposure"] == pl.Boolean:
            raise TypeError("target net_exposure must have a numeric non-boolean type")
        values = exposures.select(
            pl.col("symbol").cast(pl.String),
            pl.col("currency").cast(pl.String).str.to_uppercase(),
            pl.col("net_exposure").cast(pl.Float64),
        )
        if any(values.null_count().row(0)):
            raise ValueError("target exposure values cannot be null")
        if values["symbol"].n_unique() != values.height:
            raise ValueError("target exposures require one row per symbol")
        if values.select((pl.col("symbol").str.len_chars() == 0).any()).item():
            raise ValueError("target exposure symbols cannot be empty")
        if not values.select(pl.col("net_exposure").is_finite().all()).item():
            raise ValueError("target net_exposure values must be finite")
        if values["currency"].unique().to_list() != [context.base_currency]:
            raise ValueError("target exposures must be translated to the calculation base currency")
        return values

    def _contracts(self, contracts: Sequence[Contract], symbols: set[str]) -> dict[str, Contract]:
        if not isinstance(contracts, Sequence) or isinstance(contracts, (str, bytes)):
            raise TypeError("target contracts must be a sequence")
        contract_by_symbol: dict[str, Contract] = {}
        for contract in contracts:
            if not isinstance(contract, Contract):
                raise TypeError("target contracts must contain Contract objects")
            if contract.symbol in contract_by_symbol:
                raise ValueError(f"duplicate target contract: {contract.symbol}")
            if Contract.get(contract.symbol) is not contract:
                raise ValueError(f"target contract is not canonically registered: {contract.symbol}")
            contract_by_symbol[contract.symbol] = contract
        if set(contract_by_symbol) != symbols:
            raise ValueError("target contracts must exactly match exposure symbols")
        if missing := symbols - set(self.unit_rules):
            raise ValueError(f"tradable unit rules are missing symbols: {', '.join(sorted(missing))}")
        return contract_by_symbol

    @staticmethod
    def _prices(
        prices: pl.DataFrame,
        contracts: Mapping[str, Contract],
        context: CalculationContext,
    ) -> dict[str, tuple[float, np.datetime64]]:
        if not isinstance(prices, pl.DataFrame):
            raise TypeError("target prices must be a Polars DataFrame")
        required = {"symbol", "currency", "price", "as_of"}
        if missing := required - set(prices.columns):
            raise ValueError(f"target price columns are missing: {', '.join(sorted(missing))}")
        as_of_dtype = prices.schema["as_of"]
        if as_of_dtype != pl.Date and not isinstance(as_of_dtype, pl.Datetime):
            raise TypeError("target price as_of must be Polars Date or Datetime")
        if isinstance(as_of_dtype, pl.Datetime) and as_of_dtype.time_zone is not None:
            raise ValueError("target price as_of values must be timezone-naive")
        if not prices.schema["price"].is_numeric() or prices.schema["price"] == pl.Boolean:
            raise TypeError("target prices must have a numeric non-boolean type")
        values = prices.select(
            pl.col("symbol").cast(pl.String),
            pl.col("currency").cast(pl.String).str.to_uppercase(),
            pl.col("price").cast(pl.Float64),
            pl.col("as_of").cast(pl.Datetime("ns")),
        )
        if values.is_empty() or any(values.null_count().row(0)):
            raise ValueError("target price values cannot be empty or null")
        if values["symbol"].n_unique() != values.height:
            raise ValueError("target prices require one row per symbol")
        if set(values["symbol"].to_list()) != set(contracts):
            raise ValueError("target prices must exactly match exposure symbols")
        for symbol, currency in values.select("symbol", "currency").iter_rows():
            if currency != contracts[symbol].instrument_spec.currency:
                raise ValueError(f"target price currency does not match the contract for {symbol}")
        if values.select((~pl.col("price").is_finite() | (pl.col("price") <= 0)).any()).item():
            raise ValueError("target prices must be finite and positive")
        if not context.allow_lookahead and values.select(
            (pl.col("as_of") > pl.lit(context.market_data_as_of)).any()
        ).item():
            raise ValueError("target prices use market data after the calculation cutoff")
        return {
            symbol: (price, np.datetime64(as_of, "ns"))
            for symbol, _currency, price, as_of in values.iter_rows()
        }

    @staticmethod
    def _validate_fx(fx: FxRateSnapshot, context: CalculationContext) -> None:
        if not isinstance(fx, FxRateSnapshot):
            raise TypeError("target FX must be an FxRateSnapshot")
        if fx.base_currency != context.base_currency:
            raise ValueError("target FX and calculation context base currencies must match")
        if not context.allow_lookahead and fx.as_of > context.market_data_as_of:
            raise ValueError("target FX uses market data after the calculation cutoff")

    @staticmethod
    def _pending(
        pending_orders: Sequence[Order], contracts: Mapping[str, Contract],
    ) -> dict[str, int]:
        if not isinstance(pending_orders, Sequence) or isinstance(pending_orders, (str, bytes)):
            raise TypeError("pending target orders must be a sequence")
        quantities: dict[str, int] = {}
        for order in pending_orders:
            if not isinstance(order, Order):
                raise TypeError("pending target orders must contain Order objects")
            _validate_order_references(order)
            if not order.is_open() or order.contract.symbol not in contracts:
                continue
            if order.contract is not contracts[order.contract.symbol]:
                raise ValueError(f"pending target order contract identity does not match {order.contract.symbol}")
            quantity = _whole_quantity(order.qty, field_name="pending target order qty")
            quantities[order.contract.symbol] = quantities.get(order.contract.symbol, 0) + quantity
        return quantities

    @staticmethod
    def _position(account: Account, contract: Contract, timestamp: np.datetime64) -> int:
        positions = account.positions(contract.contract_group, timestamp)
        quantity = next((value for item, value in positions if item is contract), 0)
        if math.isclose(quantity, 0.0):
            return 0
        return _whole_quantity(quantity, field_name="current target position")


TargetInputProvider = Callable[[np.datetime64, Account, Any], ExecutableTargetInputs]


@dataclass
class ExecutableTargetRule:
    """Adapt point-in-time executable targets to Strategy's rule contract."""

    builder: ExecutableTargetBuilder
    contracts: Sequence[Contract]
    inputs: TargetInputProvider
    risk_measures: Sequence[RiskMeasure] = ()
    risk_policies: Sequence[RiskPolicy] = ()
    _latest_result: ExecutableTargetResult | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.builder, ExecutableTargetBuilder):
            raise TypeError("executable target rule builder must be an ExecutableTargetBuilder")
        if not isinstance(self.contracts, Sequence) or isinstance(self.contracts, (str, bytes)):
            raise TypeError("executable target rule contracts must be a sequence")
        self.contracts = tuple(self.contracts)
        if not self.contracts or any(not isinstance(contract, Contract) for contract in self.contracts):
            raise TypeError("executable target rule contracts must contain Contract objects")
        if not callable(self.inputs):
            raise TypeError("executable target rule inputs must be callable")
        if not isinstance(self.risk_measures, Sequence) or isinstance(self.risk_measures, (str, bytes)):
            raise TypeError("executable target rule risk_measures must be a sequence")
        self.risk_measures = tuple(self.risk_measures)
        self.risk_policies = self.builder._policies(self.risk_policies)

    @property
    def latest_result(self) -> ExecutableTargetResult:
        """Return the latest detached target construction and admission evidence."""
        if self._latest_result is None:
            raise RuntimeError("executable target rule has not been evaluated")
        return self._latest_result

    def __call__(
        self,
        contract_group: ContractGroup,
        index: int,
        timestamps: np.ndarray,
        _indicators: Any,
        _signal: np.ndarray,
        account: Account,
        pending_orders: Sequence[Order],
        strategy_context: Any,
    ) -> list[Order]:
        timestamp = cast(np.datetime64, timestamps[index])
        if any(contract.contract_group is not contract_group for contract in self.contracts):
            raise ValueError("executable target rule contracts must belong to the callback contract group")
        inputs = self.inputs(timestamp, account, strategy_context)
        if not isinstance(inputs, ExecutableTargetInputs):
            raise TypeError("executable target rule input provider must return ExecutableTargetInputs")
        if inputs.calculation.valuation_time != timestamp:
            raise ValueError("executable target rule calculation time must match the callback timestamp")
        result = self.builder.build(
            inputs.exposures,
            self.contracts,
            inputs.prices,
            inputs.fx,
            inputs.calculation,
            account,
            pending_orders=pending_orders,
            risk_measures=self.risk_measures,
            risk_policies=self.risk_policies,
        )
        self._latest_result = result
        return list(result.orders)


__all__ = [
    "ExecutableTargetBuilder",
    "ExecutableTargetInputs",
    "ExecutableTargetResult",
    "ExecutableTargetRule",
    "TargetInputProvider",
    "TargetRounding",
    "TradableUnitRule",
]
