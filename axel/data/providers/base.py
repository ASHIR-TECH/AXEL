"""
Provider contracts shared by every asset class.

Two interfaces intentionally coexist:

* ``Provider`` / ``ProviderResult`` -- the Phase 2 synchronous ingestion surface
  used by the existing Alpaca/FRED/SEC/GDELT adapters.
* ``MarketDataProvider`` / ``CapabilityAwareProvider`` -- the cross-asset async
  surface used by research and live ingestion.

An adapter must declare its capabilities. Calling an operation it does not
support raises ``CapabilityError``; it must never return empty data that looks
like a valid observation.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from axel.data.instruments import Instrument
from axel.data.marketdata import (
    FundingRateObservation,
    OpenInterestObservation,
    OrderBookSnapshot,
    QuoteTick,
    TradeTick,
)
from axel.data.schemas import BarRecord, CanonicalRecord, RawPayload


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


class Capability(StrEnum):
    """Operations a market-data provider may support."""

    INSTRUMENTS = "instruments"
    HISTORICAL_BARS = "historical_bars"
    TRADES = "trades"
    ORDER_BOOK = "order_book"
    QUOTES = "quotes"
    FUNDING_RATES = "funding_rates"
    OPEN_INTEREST = "open_interest"
    PREDICTION_MARKETS = "prediction_markets"
    PREDICTION_QUOTES = "prediction_quotes"
    PREDICTION_ORDER_BOOK = "prediction_order_book"
    PREDICTION_TRADES = "prediction_trades"
    PREDICTION_BARS = "prediction_bars"
    HEALTH = "health"


class CapabilityError(NotImplementedError):
    """Raised when a provider is asked for an operation it does not support."""

    def __init__(self, provider: str, capability: Capability, detail: str = "") -> None:
        self.provider = provider
        self.capability = capability
        message = f"provider {provider!r} does not support capability {capability.value!r}"
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


class ProviderRequestError(RuntimeError):
    """A provider request failed; callers must fail closed, not fabricate data."""

    def __init__(
        self,
        provider: str,
        operation: str,
        detail: str,
        *,
        status_code: int | None = None,
    ) -> None:
        self.provider = provider
        self.operation = operation
        self.status_code = status_code
        message = f"{provider} {operation} failed: {detail}"
        if status_code is not None:
            message = f"{message} (HTTP {status_code})"
        super().__init__(message)


class ProviderDataError(ValueError):
    """A provider returned a payload that cannot be safely normalised."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """The explicitly declared capability set for an adapter."""

    declared: frozenset[Capability] = field(default_factory=frozenset)

    def supports(self, capability: Capability) -> bool:
        return capability in self.declared

    def require(self, capability: Capability, provider: str) -> None:
        if not self.supports(capability):
            raise CapabilityError(provider, capability)

    def __contains__(self, capability: object) -> bool:
        return capability in self.declared

    def __iter__(self):
        return iter(sorted(self.declared, key=lambda item: item.value))


@dataclass(frozen=True)
class ProviderHealth:
    """Result of a provider health probe."""

    provider: str
    healthy: bool
    checked_at: datetime
    latency_ms: float | None = None
    message: str = ""
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("health probe requires a provider name")
        if self.checked_at.tzinfo is None:
            raise ValueError("checked_at must be timezone-aware")
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")

    def require_healthy(self) -> None:
        if not self.healthy:
            raise ProviderRequestError(self.provider, "health_check", self.message or "unhealthy")


@runtime_checkable
class MarketDataProvider(Protocol):
    """Async, capability-aware provider interface used across asset classes."""

    name: str
    capabilities: ProviderCapabilities

    async def get_instruments(self) -> Sequence[Instrument]: ...

    async def get_historical_bars(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1d",
    ) -> Sequence[BarRecord]: ...

    async def get_trades(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[TradeTick]: ...

    async def get_order_book(
        self,
        instrument_id: str,
        *,
        depth: int = 5,
    ) -> OrderBookSnapshot: ...

    async def get_quote(self, instrument_id: str) -> QuoteTick: ...

    async def get_funding_rates(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[FundingRateObservation]: ...

    async def get_open_interest(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[OpenInterestObservation]: ...

    async def health_check(self) -> ProviderHealth: ...


class CapabilityAwareProvider(ABC):
    """Base adapter: unsupported operations raise, never silently return empty."""

    name: str = "unknown"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def require_capability(self, capability: Capability) -> None:
        self.capabilities.require(capability, self.name)

    def _unsupported(self, capability: Capability) -> CapabilityError:
        return CapabilityError(self.name, capability)

    @staticmethod
    def validate_window(start_time: datetime, end_time: datetime) -> None:
        for label, value in (("start_time", start_time), ("end_time", end_time)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if end_time <= start_time:
            raise ValueError("end_time must be after start_time")

    async def get_instruments(self) -> Sequence[Instrument]:
        raise self._unsupported(Capability.INSTRUMENTS)

    async def get_historical_bars(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = "1d",
    ) -> Sequence[BarRecord]:
        raise self._unsupported(Capability.HISTORICAL_BARS)

    async def get_trades(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[TradeTick]:
        raise self._unsupported(Capability.TRADES)

    async def get_order_book(
        self,
        instrument_id: str,
        *,
        depth: int = 5,
    ) -> OrderBookSnapshot:
        raise self._unsupported(Capability.ORDER_BOOK)

    async def get_quote(self, instrument_id: str) -> QuoteTick:
        raise self._unsupported(Capability.QUOTES)

    async def get_funding_rates(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[FundingRateObservation]:
        raise self._unsupported(Capability.FUNDING_RATES)

    async def get_open_interest(
        self,
        instrument_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[OpenInterestObservation]:
        raise self._unsupported(Capability.OPEN_INTEREST)

    async def health_check(self) -> ProviderHealth:
        raise self._unsupported(Capability.HEALTH)

    async def close(self) -> None:
        """Release network resources; default is a no-op."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()


__all__ = [
    "Capability",
    "CapabilityAwareProvider",
    "CapabilityError",
    "MarketDataProvider",
    "Provider",
    "ProviderCapabilities",
    "ProviderDataError",
    "ProviderHealth",
    "ProviderRequestError",
    "ProviderResult",
    "now_utc",
    "require_api_key",
]
