"""Deterministic, fingerprinted execution-cost sensitivity experiments."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from numbers import Integral, Real
from types import MappingProxyType
from typing import TypeAlias

import polars as pl

SensitivityScalar: TypeAlias = str | int | float | bool | None
SensitivityEvaluator: TypeAlias = Callable[["CostSensitivityCase"], Mapping[str, Real]]


def _sha256(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    try:
        decoded = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest") from exc
    if len(decoded) != 32:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal SHA-256 digest")
    return value.lower()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _assumptions(values: Mapping[str, SensitivityScalar]) -> Mapping[str, SensitivityScalar]:
    if not isinstance(values, Mapping):
        raise TypeError("cost sensitivity assumptions must be a mapping")
    normalized: dict[str, SensitivityScalar] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key or key != key.strip():
            raise ValueError("cost sensitivity assumption names must be non-empty trimmed strings")
        if isinstance(value, str):
            if not value:
                raise ValueError(f"cost sensitivity assumption {key} cannot be empty")
            normalized[key] = value
        elif type(value) is bool or value is None:
            normalized[key] = value
        elif isinstance(value, Integral):
            normalized[key] = int(value)
        elif isinstance(value, Real):
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"cost sensitivity assumption {key} must be finite")
            normalized[key] = float(value)
        else:
            raise TypeError(f"cost sensitivity assumption {key} must be a JSON scalar")
    if not normalized:
        raise ValueError("cost sensitivity assumptions cannot be empty")
    return MappingProxyType(dict(sorted(normalized.items())))


class CostSensitivityVariant(str, Enum):
    """Execution-target variant used in paired comparisons."""

    UNBUFFERED = "unbuffered"
    BUFFERED = "buffered"


_RESULT_SCHEMA = {
    "case_index": pl.UInt32,
    "comparison_id": pl.String,
    "variant": pl.String,
    "seed": pl.UInt64,
    "input_sha256": pl.String,
    "strategy_sha256": pl.String,
    "case_sha256": pl.String,
    "assumptions_json": pl.String,
    "metric": pl.String,
    "value": pl.Float64,
}


@dataclass(frozen=True)
class CostSensitivityCase:
    """One immutable and fully recorded experiment case."""

    comparison_id: str
    variant: CostSensitivityVariant
    assumptions: Mapping[str, SensitivityScalar]
    seed: int = 0
    _assumptions_json: str = field(init=False, repr=False)
    _case_sha256: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.comparison_id, str)
            or not self.comparison_id
            or self.comparison_id != self.comparison_id.strip()
        ):
            raise ValueError("cost sensitivity comparison_id must be a non-empty trimmed string")
        if not isinstance(self.variant, CostSensitivityVariant):
            raise TypeError("cost sensitivity variant must be CostSensitivityVariant")
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, Integral)
            or not 0 <= self.seed <= 2**64 - 1
        ):
            raise ValueError("cost sensitivity seed must be an unsigned 64-bit integer")
        normalized = _assumptions(self.assumptions)
        assumptions_json = _canonical_json(dict(normalized))
        identity = {
            "comparison_id": self.comparison_id,
            "variant": self.variant.value,
            "seed": int(self.seed),
            "assumptions": dict(normalized),
        }
        object.__setattr__(self, "seed", int(self.seed))
        object.__setattr__(self, "assumptions", normalized)
        object.__setattr__(self, "_assumptions_json", assumptions_json)
        object.__setattr__(self, "_case_sha256", _digest(identity))

    @property
    def assumptions_json(self) -> str:
        return self._assumptions_json

    @property
    def case_sha256(self) -> str:
        return self._case_sha256


class CostSensitivityEvaluationError(RuntimeError):
    """A sensitivity evaluator failed for a named case."""


@dataclass(frozen=True, init=False)
class CostSensitivityResult:
    """Detached long-form sensitivity outcomes and paired comparisons."""

    input_sha256: str
    strategy_sha256: str
    _data: pl.DataFrame

    def __init__(self, input_sha256: str, strategy_sha256: str, data: pl.DataFrame) -> None:
        input_sha256 = _sha256(input_sha256, field_name="input fingerprint")
        strategy_sha256 = _sha256(strategy_sha256, field_name="strategy fingerprint")
        if not isinstance(data, pl.DataFrame):
            raise TypeError("cost sensitivity result data must be a Polars DataFrame")
        if set(data.columns) != set(_RESULT_SCHEMA):
            raise ValueError("cost sensitivity result columns do not match the required schema")
        if any(data.schema[name] != dtype for name, dtype in _RESULT_SCHEMA.items()):
            raise TypeError("cost sensitivity result column types do not match the required schema")
        normalized = data.select(*_RESULT_SCHEMA)
        if normalized.is_empty():
            raise ValueError("cost sensitivity result data cannot be empty")
        if any(normalized.null_count().row(0)):
            raise ValueError("cost sensitivity result values cannot be null")
        if not normalized.select(pl.col("value").is_finite().all()).item():
            raise ValueError("cost sensitivity result metric values must be finite")
        if set(normalized["variant"].unique()) - {variant.value for variant in CostSensitivityVariant}:
            raise ValueError("cost sensitivity result contains an unknown variant")
        if normalized.select(
            (
                (pl.col("comparison_id").str.len_chars() == 0)
                | (pl.col("comparison_id") != pl.col("comparison_id").str.strip_chars())
                | (pl.col("metric").str.len_chars() == 0)
                | (pl.col("metric") != pl.col("metric").str.strip_chars())
            ).any()
        ).item():
            raise ValueError("cost sensitivity result identities must be non-empty and trimmed")
        if normalized["input_sha256"].unique().to_list() != [input_sha256]:
            raise ValueError("cost sensitivity result input fingerprint does not match")
        if normalized["strategy_sha256"].unique().to_list() != [strategy_sha256]:
            raise ValueError("cost sensitivity result strategy fingerprint does not match")
        if not normalized.equals(normalized.sort("case_index", "metric")):
            raise ValueError("cost sensitivity result rows must be ordered by case_index and metric")

        metric_names: tuple[str, ...] | None = None
        case_indices = normalized["case_index"].unique(maintain_order=True).to_list()
        if case_indices != list(range(len(case_indices))):
            raise ValueError("cost sensitivity result case indices must be contiguous from zero")
        metadata_columns = tuple(name for name in _RESULT_SCHEMA if name not in {"metric", "value"})
        case_metadata = normalized.select(*metadata_columns).unique(subset="case_index", maintain_order=True)
        if case_metadata.select(pl.struct("comparison_id", "variant").is_duplicated().any()).item():
            raise ValueError("cost sensitivity result comparison_id and variant pairs must be unique")
        for case_index in case_indices:
            case_rows = normalized.filter(pl.col("case_index") == case_index)
            if case_rows.select(*metadata_columns).unique().height != 1:
                raise ValueError("cost sensitivity result case metadata must be consistent across metrics")
            current_metrics = tuple(case_rows["metric"].to_list())
            if len(set(current_metrics)) != len(current_metrics):
                raise ValueError("cost sensitivity result metrics must be unique within each case")
            if metric_names is None:
                metric_names = current_metrics
            elif current_metrics != metric_names:
                raise ValueError("cost sensitivity result cases must contain the same metrics")
            metadata = case_rows.row(0, named=True)
            try:
                assumptions = json.loads(metadata["assumptions_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("cost sensitivity result assumptions_json is invalid") from exc
            if not isinstance(assumptions, dict) or not assumptions:
                raise ValueError("cost sensitivity result assumptions_json must contain an object")
            validated_assumptions = dict(_assumptions(assumptions))
            if _canonical_json(validated_assumptions) != metadata["assumptions_json"]:
                raise ValueError("cost sensitivity result assumptions_json must be canonical")
            identity = {
                "comparison_id": metadata["comparison_id"],
                "variant": metadata["variant"],
                "seed": metadata["seed"],
                "assumptions": validated_assumptions,
            }
            if _digest(identity) != metadata["case_sha256"]:
                raise ValueError("cost sensitivity result case fingerprint does not reconcile")
        object.__setattr__(self, "input_sha256", input_sha256)
        object.__setattr__(self, "strategy_sha256", strategy_sha256)
        object.__setattr__(self, "_data", normalized.clone())

    @property
    def data(self) -> pl.DataFrame:
        return self._data.clone()

    def compare_buffering(self, metric: str) -> pl.DataFrame:
        """Return buffered-minus-unbuffered values for complete named pairs."""
        if not isinstance(metric, str) or not metric:
            raise ValueError("cost sensitivity comparison metric must be a non-empty string")
        available_metrics = set(self._data["metric"].to_list())
        if metric not in available_metrics:
            raise ValueError(f"cost sensitivity metric is unavailable: {metric}")
        values: dict[str, dict[str, tuple[float, str]]] = {}
        for comparison_id, variant, value, case_sha256 in self._data.filter(
            pl.col("metric") == metric
        ).select("comparison_id", "variant", "value", "case_sha256").iter_rows():
            values.setdefault(comparison_id, {})[variant] = (value, case_sha256)
        rows = []
        for comparison_id, variants in sorted(values.items()):
            if not {CostSensitivityVariant.UNBUFFERED.value, CostSensitivityVariant.BUFFERED.value} <= set(
                variants
            ):
                continue
            unbuffered, unbuffered_sha256 = variants[CostSensitivityVariant.UNBUFFERED.value]
            buffered, buffered_sha256 = variants[CostSensitivityVariant.BUFFERED.value]
            rows.append(
                {
                    "comparison_id": comparison_id,
                    "metric": metric,
                    "unbuffered_value": unbuffered,
                    "buffered_value": buffered,
                    "difference": buffered - unbuffered,
                    "unbuffered_case_sha256": unbuffered_sha256,
                    "buffered_case_sha256": buffered_sha256,
                }
            )
        return pl.DataFrame(
            rows,
            schema={
                "comparison_id": pl.String,
                "metric": pl.String,
                "unbuffered_value": pl.Float64,
                "buffered_value": pl.Float64,
                "difference": pl.Float64,
                "unbuffered_case_sha256": pl.String,
                "buffered_case_sha256": pl.String,
            },
        )


@dataclass(frozen=True)
class CostSensitivityRunner:
    """Evaluate independent cases in deterministic declared order."""

    input_sha256: str
    strategy_sha256: str
    cases: Sequence[CostSensitivityCase]

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_sha256", _sha256(self.input_sha256, field_name="input fingerprint"))
        object.__setattr__(
            self,
            "strategy_sha256",
            _sha256(self.strategy_sha256, field_name="strategy fingerprint"),
        )
        if not isinstance(self.cases, Sequence) or isinstance(self.cases, (str, bytes)):
            raise TypeError("cost sensitivity cases must be a sequence")
        cases = tuple(self.cases)
        if not cases:
            raise ValueError("cost sensitivity cases cannot be empty")
        if any(not isinstance(case, CostSensitivityCase) for case in cases):
            raise TypeError("cost sensitivity cases must contain CostSensitivityCase values")
        identities = [(case.comparison_id, case.variant) for case in cases]
        if len(set(identities)) != len(identities):
            raise ValueError("cost sensitivity comparison_id and variant pairs must be unique")
        object.__setattr__(self, "cases", cases)

    def run(self, evaluate: SensitivityEvaluator) -> CostSensitivityResult:
        """Execute every case once and retain finite, comparable metrics."""
        if not callable(evaluate):
            raise TypeError("cost sensitivity evaluator must be callable")
        rows: list[dict[str, object]] = []
        expected_metrics: tuple[str, ...] | None = None
        for case_index, case in enumerate(self.cases):
            case_label = f"{case.comparison_id}:{case.variant.value}"
            try:
                raw_metrics = evaluate(case)
            except Exception as exc:
                raise CostSensitivityEvaluationError(
                    f"cost sensitivity evaluation failed for {case_label}"
                ) from exc
            if not isinstance(raw_metrics, Mapping) or not raw_metrics:
                raise TypeError(
                    f"cost sensitivity evaluator must return a non-empty metric mapping for {case_label}"
                )
            metrics: dict[str, float] = {}
            for name, value in raw_metrics.items():
                if not isinstance(name, str) or not name or name != name.strip():
                    raise ValueError(
                        f"cost sensitivity metric names must be non-empty trimmed strings for {case_label}"
                    )
                if isinstance(value, bool) or not isinstance(value, Real):
                    raise TypeError(f"cost sensitivity metric {name} must be a real number for {case_label}")
                try:
                    finite = math.isfinite(value)
                except OverflowError:
                    finite = False
                if not finite:
                    raise ValueError(f"cost sensitivity metric {name} must be finite for {case_label}")
                metrics[name] = float(value)
            metric_names = tuple(sorted(metrics))
            if expected_metrics is None:
                expected_metrics = metric_names
            elif metric_names != expected_metrics:
                raise ValueError(
                    f"cost sensitivity cases must return the same metric names; mismatch at {case_label}"
                )
            for metric in metric_names:
                rows.append(
                    {
                        "case_index": case_index,
                        "comparison_id": case.comparison_id,
                        "variant": case.variant.value,
                        "seed": case.seed,
                        "input_sha256": self.input_sha256,
                        "strategy_sha256": self.strategy_sha256,
                        "case_sha256": case.case_sha256,
                        "assumptions_json": case.assumptions_json,
                        "metric": metric,
                        "value": metrics[metric],
                    }
                )
        data = pl.DataFrame(
            rows,
            schema_overrides={
                "case_index": pl.UInt32,
                "comparison_id": pl.String,
                "variant": pl.String,
                "seed": pl.UInt64,
                "input_sha256": pl.String,
                "strategy_sha256": pl.String,
                "case_sha256": pl.String,
                "assumptions_json": pl.String,
                "metric": pl.String,
                "value": pl.Float64,
            },
        )
        return CostSensitivityResult(self.input_sha256, self.strategy_sha256, data)


__all__ = [
    "CostSensitivityCase",
    "CostSensitivityEvaluationError",
    "CostSensitivityResult",
    "CostSensitivityRunner",
    "CostSensitivityVariant",
]
