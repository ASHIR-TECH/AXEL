"""
Deterministic serialization and fingerprint helpers for Phase 11.

Fingerprints are used for idempotent snapshot, exposure, proposal, and
reconciliation identifiers. They are not a substitute for database uniqueness
constraints, but they make replaying the same logical input produce the same
identifier.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from math import isfinite
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Recursively convert domain objects into JSON-safe primitives.

    Raises ``ValueError`` for NaN or infinity so malformed numerics cannot be
    persisted or hashed as if they were valid.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cannot serialize naive datetime")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return {str(key): to_jsonable(item) for key, item in items}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("cannot serialize non-finite float")
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)


def canonical_json(value: Any) -> str:
    """Stable JSON representation with sorted keys and no whitespace."""
    return json.dumps(
        to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def stable_hash(*parts: Any) -> str:
    """SHA-256 over the canonical representation of one or more parts."""
    payload = canonical_json(parts if len(parts) != 1 else parts[0])
    return sha256(payload.encode("utf-8")).hexdigest()


def fingerprint(prefix: str, *parts: Any) -> str:
    """Prefixed deterministic identifier, e.g. ``snap_<24 hex chars>``."""
    if not prefix:
        raise ValueError("fingerprint prefix must be non-empty")
    return f"{prefix}_{stable_hash(*parts)[:24]}"


__all__ = ["canonical_json", "fingerprint", "stable_hash", "to_jsonable"]
