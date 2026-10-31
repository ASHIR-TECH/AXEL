"""
Versioned event envelope and idempotent event log.

The envelope is the shared contract between Developer 1's data producers and
every downstream consumer (features, research, portfolio, execution). It keeps
the four clocks separate:

* ``event_timestamp`` -- when the fact happened.
* ``published_timestamp`` -- when the source published it.
* ``received_timestamp`` -- when AXEL received it.
* ``available_timestamp`` -- when it may legitimately be used.

For a historical backfill, ``available_timestamp`` is derived from publication,
not from the wall-clock receipt time; otherwise all history would be unusable in
a backtest. Live consumers must additionally check ``received_timestamp`` against
their decision clock.

Idempotency is explicit: appending the same ``event_id`` and payload twice is a
no-op; appending the same ID with different content is a hard conflict.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from threading import RLock
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from axel.data.marketdata import (
    MarketDataRecord,
    market_record_to_dict,
)
from axel.data.schemas import (
    CanonicalRecord,
    record_to_dict,
    require_tz,
)

EVENT_SCHEMA_VERSION = "event-1"


class EventType(StrEnum):
    INSTRUMENT = "instrument"
    BAR = "bar"
    TRADE = "trade"
    QUOTE = "quote"
    ORDER_BOOK = "order_book"
    FUNDING_RATE = "funding_rate"
    OPEN_INTEREST = "open_interest"
    MACRO = "macro"
    FILING = "filing"
    NEWS = "news"
    PREDICTION_QUOTE = "prediction_quote"
    OPTION_QUOTE = "option_quote"
    RESEARCH_SIGNAL = "research_signal"


class EventConflictError(ValueError):
    """The same event_id was reused for different content."""


class AppendOutcome(StrEnum):
    APPENDED = "appended"
    DUPLICATE = "duplicate"


def canonical_payload_json(payload: Mapping[str, Any]) -> str:
    """Deterministic JSON used for hashing and duplicate detection."""
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"event payload is not canonical-JSON serialisable: {exc}") from exc


def _json_default(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "value") and isinstance(value.value, (str, int, float, bool)):
        return value.value
    raise TypeError(f"unsupported payload value: {type(value)!r}")


def payload_hash(payload: Mapping[str, Any]) -> str:
    return sha256(canonical_payload_json(payload).encode("utf-8")).hexdigest()


def make_event_id(
    source: str,
    event_type: str,
    instrument_id: str | None,
    event_timestamp: datetime,
    content_hash: str,
) -> str:
    require_tz(event_timestamp, "event_timestamp")
    material = "|".join(
        (source, event_type, instrument_id or "", event_timestamp.isoformat(), content_hash)
    )
    return sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Immutable, versioned envelope for one point-in-time fact."""

    event_id: str
    event_type: str
    source: str
    event_timestamp: datetime
    published_timestamp: datetime
    received_timestamp: datetime
    payload: Mapping[str, Any]
    schema_version: str = EVENT_SCHEMA_VERSION
    instrument_id: str | None = None
    available_timestamp: datetime | None = None
    payload_hash: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("event_id", self.event_id),
            ("event_type", self.event_type),
            ("source", self.source),
            ("schema_version", self.schema_version),
        ):
            if not value or not value.strip():
                raise ValueError(f"{name} is required")
        for name, value in (
            ("event_timestamp", self.event_timestamp),
            ("published_timestamp", self.published_timestamp),
            ("received_timestamp", self.received_timestamp),
        ):
            require_tz(value, name)
        if self.published_timestamp < self.event_timestamp:
            raise ValueError("published_timestamp cannot precede event_timestamp")
        if self.received_timestamp < self.event_timestamp:
            raise ValueError("received_timestamp cannot precede event_timestamp")
        available = self.available_timestamp or max(
            self.event_timestamp, self.published_timestamp
        )
        require_tz(available, "available_timestamp")
        if available < self.event_timestamp:
            raise ValueError("available_timestamp cannot precede event_timestamp")
        if available < self.published_timestamp:
            raise ValueError("available_timestamp cannot precede published_timestamp")

        detached = json.loads(canonical_payload_json(self.payload))
        object.__setattr__(self, "payload", MappingProxyType(detached))
        computed = payload_hash(self.payload)
        if self.payload_hash and self.payload_hash != computed:
            raise ValueError("payload_hash does not match payload content")
        object.__setattr__(self, "payload_hash", computed)
        object.__setattr__(self, "available_timestamp", available)

    @property
    def payload_json(self) -> str:
        return canonical_payload_json(self.payload)

    def is_available_at(self, at: datetime) -> bool:
        require_tz(at, "at")
        return self.available_timestamp is not None and self.available_timestamp <= at

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "source": self.source,
            "instrument_id": self.instrument_id,
            "event_timestamp": self.event_timestamp.isoformat(),
            "published_timestamp": self.published_timestamp.isoformat(),
            "received_timestamp": self.received_timestamp.isoformat(),
            "available_timestamp": (
                self.available_timestamp.isoformat() if self.available_timestamp else None
            ),
            "payload": dict(self.payload),
            "payload_hash": self.payload_hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EventEnvelope:
        available = data.get("available_timestamp")
        return cls(
            event_id=str(data["event_id"]),
            event_type=str(data["event_type"]),
            schema_version=str(data.get("schema_version", EVENT_SCHEMA_VERSION)),
            source=str(data["source"]),
            instrument_id=data.get("instrument_id"),
            event_timestamp=datetime.fromisoformat(str(data["event_timestamp"])),
            published_timestamp=datetime.fromisoformat(str(data["published_timestamp"])),
            received_timestamp=datetime.fromisoformat(str(data["received_timestamp"])),
            available_timestamp=datetime.fromisoformat(available) if available else None,
            payload=dict(data.get("payload", {})),
            payload_hash=str(data.get("payload_hash", "")),
        )


def envelope_for_record(
    record: CanonicalRecord,
    *,
    event_type: str | None = None,
    source: str | None = None,
    received_timestamp: datetime | None = None,
) -> EventEnvelope:
    """Wrap an existing canonical record without changing its PIT semantics."""
    payload = record_to_dict(record)
    event_type = event_type or record.record_type
    source = source or record.provenance.source
    content_hash = payload_hash(payload)
    return EventEnvelope(
        event_id=make_event_id(
            source, event_type, getattr(record, "instrument_id", None), record.event_time, content_hash
        ),
        event_type=event_type,
        source=source,
        instrument_id=getattr(record, "instrument_id", None),
        event_timestamp=record.event_time,
        published_timestamp=record.available_at,
        received_timestamp=received_timestamp or record.provenance.ingestion_timestamp,
        available_timestamp=record.available_at,
        payload=payload,
    )


def envelope_for_market_record(
    record: MarketDataRecord,
    *,
    received_timestamp: datetime | None = None,
) -> EventEnvelope:
    payload = market_record_to_dict(record)
    content_hash = payload_hash(payload)
    return EventEnvelope(
        event_id=make_event_id(
            record.provenance.source,
            record.record_type,
            record.instrument_id,
            record.event_time,
            content_hash,
        ),
        event_type=record.record_type,
        source=record.provenance.source,
        instrument_id=record.instrument_id,
        event_timestamp=record.event_time,
        published_timestamp=record.available_at,
        received_timestamp=received_timestamp or record.provenance.ingestion_timestamp,
        available_timestamp=record.available_at,
        payload=payload,
    )


@runtime_checkable
class EventLog(Protocol):
    """Minimal append-only event-log contract."""

    def append(self, event: EventEnvelope) -> AppendOutcome: ...

    def get(self, event_id: str) -> EventEnvelope | None: ...

    def replay(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        event_type: str | None = None,
    ) -> Iterator[EventEnvelope]: ...

    def __len__(self) -> int: ...


@dataclass
class InMemoryEventLog:
    """Deterministic, thread-safe event log for tests and single-process replay.

    Production deployments should persist the same envelope in durable storage;
    this class deliberately does not pretend to be a database.
    """

    _events: dict[str, EventEnvelope] = field(default_factory=dict, init=False)
    _lock: RLock = field(default_factory=RLock, init=False)

    def append(self, event: EventEnvelope) -> AppendOutcome:
        with self._lock:
            existing = self._events.get(event.event_id)
            if existing is None:
                self._events[event.event_id] = event
                return AppendOutcome.APPENDED
            if existing.payload_hash == event.payload_hash:
                return AppendOutcome.DUPLICATE
            raise EventConflictError(
                f"event_id {event.event_id!r} already exists with a different payload"
            )

    def extend(self, events: Iterable[EventEnvelope]) -> tuple[AppendOutcome, ...]:
        return tuple(self.append(event) for event in events)

    def get(self, event_id: str) -> EventEnvelope | None:
        with self._lock:
            return self._events.get(event_id)

    def replay(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        event_type: str | None = None,
    ) -> Iterator[EventEnvelope]:
        for name, value in (("since", since), ("until", until)):
            if value is not None:
                require_tz(value, name)
        with self._lock:
            events = list(self._events.values())
        selected = [
            event
            for event in events
            if (since is None or event.available_timestamp >= since)
            and (until is None or event.available_timestamp <= until)
            and (event_type is None or event.event_type == event_type)
        ]
        selected.sort(
            key=lambda event: (
                event.available_timestamp,
                event.received_timestamp,
                event.event_id,
            )
        )
        yield from selected

    def latest_available(
        self, instrument_id: str, at: datetime, *, event_type: str | None = None
    ) -> EventEnvelope | None:
        require_tz(at, "at")
        candidates = [
            event
            for event in self.replay(until=at, event_type=event_type)
            if event.instrument_id == instrument_id
        ]
        return candidates[-1] if candidates else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "AppendOutcome",
    "EventConflictError",
    "EventEnvelope",
    "EventLog",
    "EventType",
    "InMemoryEventLog",
    "canonical_payload_json",
    "envelope_for_market_record",
    "envelope_for_record",
    "make_event_id",
    "payload_hash",
]
