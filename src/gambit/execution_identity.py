"""Bounded component descriptions, not automatic serialization of arbitrary code.

Only dataclass constructor parameters and JSON scalar/container values are
captured. Callback globals, closures, external inputs and opaque state remain
explicitly unresolved. No repr(), pickle, property traversal or user hooks.
"""

from __future__ import annotations

import hashlib
import inspect
import math
from dataclasses import fields, is_dataclass
from typing import Any, cast


def describe_component(value: object) -> dict[str, Any]:
    unresolved: list[str] = []
    seen: set[int] = set()
    budget = [1000]

    def encode(item: object, path: str, depth: int) -> Any:
        budget[0] -= 1
        if depth > 8 or budget[0] < 0:
            unresolved.append(path)
            return {"unresolved": "description limit"}
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        if id(item) in seen:
            unresolved.append(path)
            return {"unresolved": "cycle"}
        seen.add(id(item))
        try:
            if type(item) in (list, tuple):
                sequence = cast(list[object] | tuple[object, ...], item)
                if len(sequence) <= 1000:
                    return [encode(child, f"{path}[{index}]", depth + 1) for index, child in enumerate(sequence)]
            if type(item) is dict and len(item) <= 1000 and all(type(key) is str for key in item):
                return {key: encode(child, f"{path}.{key}", depth + 1) for key, child in sorted(item.items())}
            target = item if inspect.isfunction(item) or inspect.ismethod(item) or inspect.isclass(item) else type(item)
            identity = f"{target.__module__}.{target.__qualname__}"
            description: dict[str, Any] = {"implementation": identity}
            try:
                source = inspect.getsource(target)
            except (OSError, TypeError):
                unresolved.append(f"{path}.source")
            else:
                description["source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
            if is_dataclass(item) and not isinstance(item, type):
                # vars avoids invoking custom field properties; slots remain unresolved.
                state = vars(item) if hasattr(item, "__dict__") else {}
                parameters = {}
                for field in fields(item):
                    if field.init:
                        if field.name in state:
                            parameters[field.name] = encode(state[field.name], f"{path}.{field.name}", depth + 1)
                        else:
                            unresolved.append(f"{path}.{field.name}")
                description["parameters"] = parameters
            else:
                unresolved.append(f"{path}.state")
            # Even inspectable source does not capture transitive/global dependencies.
            unresolved.append(f"{path}.external_dependencies")
            return description
        finally:
            seen.remove(id(item))

    return {"description": encode(value, "component", 0), "unresolved": sorted(set(unresolved))}
