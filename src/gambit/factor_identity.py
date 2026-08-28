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

    @property
    def node_key(self) -> str:
        return hashlib.sha256(self._canonical_payload).hexdigest()


__all__ = ["FactorColumnSchema", "FactorNodeIdentity"]
