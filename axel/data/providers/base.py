"""Shared provider interface and helpers for Phase 2 ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from axel.data.schemas import CanonicalRecord, RawPayload


def now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ProviderResult:
    """The immutable raw payload plus the canonical records parsed from it."""

    raw: RawPayload
    records: tuple[CanonicalRecord, ...]

    def __len__(self) -> int:
        return len(self.records)


@runtime_checkable
class Provider(Protocol):
    """Common read-only provider interface; adapters convert to canonical records."""

    name: str

    def fetch(self, *args: object, **kwargs: object) -> ProviderResult: ...


def require_api_key(value: str | None, label: str) -> str:
    if not value:
        raise ValueError(f"{label} is required")
    return value
