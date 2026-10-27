"""Deterministic conversion of domain values to JSON-compatible primitives."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any


class PrimitiveMixin:
    """Small mixin shared by immutable domain values."""

    __slots__ = ()

    def to_primitive(self) -> dict[str, Any]:
        return to_primitive(self)

    def to_json(self) -> str:
        return canonical_json(self)


def deep_freeze_primitive(value: Any) -> Any:
    """Normalize a primitive value into an immutable canonical structure.

    Scalars are retained. Sequences become tuples, mappings become sorted
    tuples of key/value pairs, and sets become deterministic tuples. Arbitrary
    objects are rejected so model fields cannot retain mutable state.
    """

    if isinstance(value, Enum):
        return deep_freeze_primitive(value.value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list)):
        return tuple(deep_freeze_primitive(item) for item in value)
    if isinstance(value, Mapping):
        return tuple(
            (str(key), deep_freeze_primitive(value[key]))
            for key in sorted(value, key=lambda item: str(item))
        )
    if isinstance(value, (set, frozenset)):
        frozen = tuple(deep_freeze_primitive(item) for item in value)
        return tuple(sorted(frozen, key=repr))
    raise TypeError(
        "expected an immutable canonical primitive, "
        f"got {type(value).__name__}"
    )


def to_primitive(value: Any) -> Any:
    """Return a deterministic JSON-compatible representation.

    Dataclass field order is retained for mappings. ``canonical_json`` also
    sorts keys, so separately constructed equivalent values serialize equally.
    Tuples become lists, and enums become their primitive values.
    """

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_primitive(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return to_primitive(value.value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (tuple, list)):
        return [to_primitive(item) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): to_primitive(value[key])
            for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (set, frozenset)):
        return [to_primitive(item) for item in sorted(value, key=repr)]
    raise TypeError(f"unsupported primitive value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialize a model or primitive value as stable compact JSON."""

    return json.dumps(
        to_primitive(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
