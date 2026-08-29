"""Deterministic identities for cached factor-DAG nodes."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeAlias

CanonicalScalar: TypeAlias = str | int | float | bool | None
CanonicalValue: TypeAlias = CanonicalScalar | Mapping[str, "CanonicalValue"] | Sequence["CanonicalValue"]

IDENTITY_FORMAT = "gambit-factor-node"
IDENTITY_VERSION = 1
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
_NAME_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,127}")


def _canonicalize(value: CanonicalValue, *, path: str) -> CanonicalScalar | list[object] | dict[str, object]:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) or not key for key in value):
            raise ValueError(f"{path} mapping keys must be non-empty strings")
        return {key: _canonicalize(value[key], path=f"{path}.{key}") for key in sorted(value)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonicalize(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(f"{path} contains unsupported value type {type(value).__name__}")


def _validate_digest(value: str, *, field_name: str) -> None:
    if _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must contain lowercase SHA-256 digests")


@dataclass(frozen=True)
class FactorColumnSchema:
    """One ordered output column in a factor-node contract."""

    name: str
    dtype: str
    nullable: bool = False

    def __post_init__(self) -> None:
        if _NAME_PATTERN.fullmatch(self.name) is None:
            raise ValueError("factor column name must be a safe portable identifier")
        if not self.dtype or len(self.dtype) > 128:
            raise ValueError("factor column dtype must be non-empty and at most 128 characters")

    def snapshot(self) -> dict[str, str | bool]:
        return {"name": self.name, "dtype": self.dtype, "nullable": self.nullable}


@dataclass(frozen=True)
class FactorNodeIdentity:
    """Complete identity inputs for one immutable factor-DAG node."""

    transform: str
    transform_version: str
    output_schema: tuple[FactorColumnSchema, ...]
    row_ordering: tuple[str, ...]
    parents: tuple[str, ...] = ()
    input_fingerprints: Mapping[str, str] = field(default_factory=dict)
    parameters: Mapping[str, CanonicalValue] = field(default_factory=dict)
    research_context: Mapping[str, CanonicalValue] = field(default_factory=dict)
    _canonical_payload: bytes = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.transform or len(self.transform) > 255:
            raise ValueError("transform must be non-empty and at most 255 characters")
        if not self.transform_version or len(self.transform_version) > 128:
            raise ValueError("transform_version must be non-empty and at most 128 characters")
        if not self.output_schema:
            raise ValueError("output_schema must not be empty")
        column_names = [column.name for column in self.output_schema]
        if len(column_names) != len(set(column_names)):
            raise ValueError("output_schema column names must be unique")
        if not self.row_ordering or any(not value for value in self.row_ordering):
            raise ValueError("row_ordering must explicitly name at least one ordering field")
        if len(self.row_ordering) != len(set(self.row_ordering)):
            raise ValueError("row_ordering fields must be unique")
        for parent in self.parents:
            _validate_digest(parent, field_name="parents")
        if len(self.parents) != len(set(self.parents)):
            raise ValueError("parents must not contain duplicate node keys")
        if not self.parents and not self.input_fingerprints:
            raise ValueError("a factor node must identify at least one parent or input")
        for name, fingerprint in self.input_fingerprints.items():
            if _NAME_PATTERN.fullmatch(name) is None:
                raise ValueError("input fingerprint names must be safe portable identifiers")
            _validate_digest(fingerprint, field_name="input_fingerprints")
        snapshot = self._build_snapshot()
        payload = json.dumps(
            snapshot,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        object.__setattr__(self, "_canonical_payload", payload)

    def _build_snapshot(self) -> dict[str, object]:
        return {
            "format": IDENTITY_FORMAT,
            "version": IDENTITY_VERSION,
            "parents": list(self.parents),
            "inputs": dict(sorted(self.input_fingerprints.items())),
            "transform": {"name": self.transform, "version": self.transform_version},
            "parameters": _canonicalize(self.parameters, path="parameters"),
            "output_schema": [column.snapshot() for column in self.output_schema],
            "row_ordering": list(self.row_ordering),
            "research_context": _canonicalize(self.research_context, path="research_context"),
        }

    def snapshot(self) -> dict[str, object]:
        """Return the canonical, JSON-compatible identity payload."""
        return json.loads(self._canonical_payload)

    @classmethod
    def from_snapshot(cls, snapshot: object) -> FactorNodeIdentity:
        """Validate and reconstruct a persisted canonical identity payload."""
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "format",
            "version",
            "parents",
            "inputs",
            "transform",
            "parameters",
            "output_schema",
            "row_ordering",
            "research_context",
        }:
            raise ValueError("factor identity snapshot fields are invalid")
        if (
            snapshot["format"] != IDENTITY_FORMAT
            or type(snapshot["version"]) is not int
            or snapshot["version"] != IDENTITY_VERSION
        ):
            raise ValueError("factor identity snapshot version is invalid")
        transform = snapshot["transform"]
        if not isinstance(transform, dict) or set(transform) != {"name", "version"}:
            raise ValueError("factor identity transform is invalid")
        parents = snapshot["parents"]
        inputs = snapshot["inputs"]
        schema = snapshot["output_schema"]
        ordering = snapshot["row_ordering"]
        parameters = snapshot["parameters"]
        context = snapshot["research_context"]
        if not isinstance(parents, list) or any(not isinstance(value, str) for value in parents):
            raise ValueError("factor identity parents are invalid")
        if not isinstance(inputs, dict):
            raise ValueError("factor identity inputs are invalid")
        if not isinstance(schema, list):
            raise ValueError("factor identity output schema is invalid")
        if not isinstance(ordering, list) or any(not isinstance(value, str) for value in ordering):
            raise ValueError("factor identity row ordering is invalid")
        if not isinstance(parameters, dict) or not isinstance(context, dict):
            raise ValueError("factor identity mappings are invalid")
        columns: list[FactorColumnSchema] = []
        for column in schema:
            if not isinstance(column, dict) or set(column) != {"name", "dtype", "nullable"}:
                raise ValueError("factor identity output schema is invalid")
            if (
                not isinstance(column["name"], str)
                or not isinstance(column["dtype"], str)
                or not isinstance(column["nullable"], bool)
            ):
                raise ValueError("factor identity output schema is invalid")
            columns.append(FactorColumnSchema(column["name"], column["dtype"], column["nullable"]))
        if not isinstance(transform["name"], str) or not isinstance(transform["version"], str):
            raise ValueError("factor identity transform is invalid")
        if any(not isinstance(name, str) or not isinstance(value, str) for name, value in inputs.items()):
            raise ValueError("factor identity inputs are invalid")
        return cls(
            transform=transform["name"],
            transform_version=transform["version"],
            parents=tuple(parents),
            input_fingerprints=inputs,
            parameters=parameters,
            output_schema=tuple(columns),
            row_ordering=tuple(ordering),
            research_context=context,
        )

    @property
    def node_key(self) -> str:
        return hashlib.sha256(self._canonical_payload).hexdigest()


__all__ = ["FactorColumnSchema", "FactorNodeIdentity"]
