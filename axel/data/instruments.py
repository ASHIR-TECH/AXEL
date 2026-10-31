"""
Canonical, asset-class-aware instrument identity.

A stock, a currency pair, a perpetual future, an option and a prediction-market
contract are not interchangeable. This module gives every one of them a stable,
versioned identity plus the metadata needed to decide whether an order can be
represented on a venue at all.

Design rules
------------
* Missing execution metadata is represented as ``None``; it is never guessed.
  ``require_execution_metadata()`` fails closed when a consumer needs it.
* Asset-class-specific fields are validated at construction time. An option
  without an expiry/strike/right is invalid, not "probably a stock".
* Provider symbols are venue-local. The canonical ``instrument_id`` is the
  cross-provider identity; a registry prevents two different instruments from
  sharing either identity.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

INSTRUMENT_SCHEMA_VERSION = "instrument-1"


class AssetClass(StrEnum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    FOREX = "forex"
    OPTION = "option"
    FUTURE = "future"
    PREDICTION = "prediction"


class ContractType(StrEnum):
    SPOT = "spot"
    CASH = "cash"
    PERPETUAL = "perpetual"
    FUTURE = "future"
    OPTION = "option"
    BINARY = "binary"
    SCALAR = "scalar"


_OPTION_RIGHTS = frozenset({"call", "put"})
_DERIVATIVE_TYPES = frozenset(
    {ContractType.PERPETUAL, ContractType.FUTURE, ContractType.OPTION, ContractType.BINARY}
)


def _require_text(value: str | None, name: str) -> None:
    if value is None or not value.strip():
        raise ValueError(f"{name} is required")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _valid_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:  # pragma: no cover - platform tzdata dependent
        raise ValueError(f"unknown timezone: {name!r}") from exc


def _canonical_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True, slots=True)
class Instrument:
    """Immutable cross-asset instrument metadata.

    ``instrument_id`` is AXEL's canonical identity. ``provider_symbol`` is the
    venue-local symbol and must not be used as a cross-provider key.
    """

    instrument_id: str
    asset_class: AssetClass
    provider_id: str
    provider_symbol: str
    venue: str
    base_currency: str | None = None
    quote_currency: str | None = None
    exchange: str | None = None
    contract_type: ContractType = ContractType.SPOT
    tick_size: float | None = None
    lot_size: float | None = None
    minimum_quantity: float | None = None
    expiry: datetime | None = None
    strike: float | None = None
    option_type: str | None = None
    settlement_currency: str | None = None
    trading_calendar: str | None = None
    timezone: str = "UTC"
    metadata_version: str = INSTRUMENT_SCHEMA_VERSION
    multiplier: float | None = None
    extra: tuple[tuple[str, str], ...] = field(default=())

    def __post_init__(self) -> None:
        _require_text(self.instrument_id, "instrument_id")
        _require_text(self.provider_id, "provider_id")
        _require_text(self.provider_symbol, "provider_symbol")
        _require_text(self.venue, "venue")
        _require_text(self.metadata_version, "metadata_version")
        _valid_timezone(self.timezone)

        if not isinstance(self.asset_class, AssetClass):
            raise TypeError("asset_class must be an AssetClass")
        if not isinstance(self.contract_type, ContractType):
            raise TypeError("contract_type must be a ContractType")

        for name, value in (
            ("tick_size", self.tick_size),
            ("lot_size", self.lot_size),
            ("multiplier", self.multiplier),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when supplied")
        if self.minimum_quantity is not None and self.minimum_quantity < 0:
            raise ValueError("minimum_quantity cannot be negative")
        if self.strike is not None and self.strike <= 0:
            raise ValueError("strike must be positive when supplied")

        if self.asset_class is AssetClass.OPTION:
            if self.contract_type is not ContractType.OPTION:
                raise ValueError("option instruments must use contract_type='option'")
            if self.expiry is None:
                raise ValueError("option instruments require an expiry")
            if self.strike is None:
                raise ValueError("option instruments require a strike")
            if self.option_type is None or self.option_type.lower() not in _OPTION_RIGHTS:
                raise ValueError("option instruments require option_type 'call' or 'put'")
        else:
            if self.option_type is not None or self.strike is not None:
                raise ValueError("strike/option_type are only valid for option instruments")
            if self.contract_type is ContractType.OPTION:
                raise ValueError("contract_type='option' requires asset_class='option'")

        if self.expiry is not None:
            _require_aware(self.expiry, "expiry")

        if self.asset_class in (AssetClass.CRYPTO, AssetClass.FOREX):
            _require_text(self.base_currency, "base_currency")
            _require_text(self.quote_currency, "quote_currency")
            if self.base_currency.upper() == self.quote_currency.upper():
                raise ValueError("base_currency and quote_currency must differ")

        if self.asset_class is AssetClass.FOREX and self.contract_type not in (
            ContractType.SPOT,
            ContractType.CASH,
            ContractType.FUTURE,
        ):
            raise ValueError("forex instruments must be spot, cash or future contracts")

        if self.asset_class is AssetClass.PREDICTION and self.contract_type not in (
            ContractType.BINARY,
            ContractType.SCALAR,
        ):
            raise ValueError("prediction instruments must be binary or scalar contracts")

        keys = [key for key, _ in self.extra]
        if len(keys) != len(set(keys)):
            raise ValueError("extra metadata keys must be unique")
        if any(not key for key in keys):
            raise ValueError("extra metadata keys cannot be empty")

    @property
    def is_derivative(self) -> bool:
        return self.contract_type in _DERIVATIVE_TYPES

    @property
    def extra_dict(self) -> dict[str, str]:
        return dict(self.extra)

    @property
    def provider_key(self) -> tuple[str, str]:
        """Venue-local identity used only for provider mapping."""
        return (self.provider_id, self.provider_symbol)

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expiry is None:
            return False
        check = now or datetime.now(UTC)
        _require_aware(check, "now")
        return check >= self.expiry

    def require_execution_metadata(self) -> None:
        """Fail closed when an execution path needs missing venue metadata."""
        missing = [
            name
            for name, value in (
                ("tick_size", self.tick_size),
                ("lot_size", self.lot_size),
                ("minimum_quantity", self.minimum_quantity),
                ("quote_currency", self.quote_currency),
                ("settlement_currency", self.settlement_currency),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                f"instrument {self.instrument_id} lacks execution metadata: {', '.join(missing)}"
            )
        if self.is_expired():
            raise ValueError(f"instrument {self.instrument_id} has expired")

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "asset_class": self.asset_class.value,
            "provider_id": self.provider_id,
            "provider_symbol": self.provider_symbol,
            "venue": self.venue,
            "base_currency": self.base_currency,
            "quote_currency": self.quote_currency,
            "exchange": self.exchange,
            "contract_type": self.contract_type.value,
            "tick_size": self.tick_size,
            "lot_size": self.lot_size,
            "minimum_quantity": self.minimum_quantity,
            "expiry": self.expiry.isoformat() if self.expiry else None,
            "strike": self.strike,
            "option_type": self.option_type,
            "settlement_currency": self.settlement_currency,
            "trading_calendar": self.trading_calendar,
            "timezone": self.timezone,
            "metadata_version": self.metadata_version,
            "multiplier": self.multiplier,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Instrument:
        expiry = data.get("expiry")
        return cls(
            instrument_id=str(data["instrument_id"]),
            asset_class=AssetClass(data["asset_class"]),
            provider_id=str(data["provider_id"]),
            provider_symbol=str(data["provider_symbol"]),
            venue=str(data["venue"]),
            base_currency=data.get("base_currency"),
            quote_currency=data.get("quote_currency"),
            exchange=data.get("exchange"),
            contract_type=ContractType(data.get("contract_type", "spot")),
            tick_size=data.get("tick_size"),
            lot_size=data.get("lot_size"),
            minimum_quantity=data.get("minimum_quantity"),
            expiry=datetime.fromisoformat(expiry) if expiry else None,
            strike=data.get("strike"),
            option_type=data.get("option_type"),
            settlement_currency=data.get("settlement_currency"),
            trading_calendar=data.get("trading_calendar"),
            timezone=str(data.get("timezone", "UTC")),
            metadata_version=str(data.get("metadata_version", INSTRUMENT_SCHEMA_VERSION)),
            multiplier=data.get("multiplier"),
            extra=tuple(sorted(dict(data.get("extra", {})).items())),
        )

    def fingerprint(self) -> str:
        """Content hash of the metadata, used to detect silent revisions."""
        return sha256(_canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


class InstrumentConflictError(ValueError):
    """Raised when an identity is reused for different instrument metadata."""


class InstrumentRegistry:
    """In-memory canonical registry with provider-symbol collision checks."""

    def __init__(self, instruments: Iterable[Instrument] = ()) -> None:
        self._by_id: dict[str, Instrument] = {}
        self._by_provider: dict[tuple[str, str], str] = {}
        for instrument in instruments:
            self.register(instrument)

    def register(self, instrument: Instrument, *, replace: bool = False) -> Instrument:
        existing = self._by_id.get(instrument.instrument_id)
        if existing is not None:
            if existing.fingerprint() == instrument.fingerprint():
                return existing
            if not replace:
                raise InstrumentConflictError(
                    f"instrument_id {instrument.instrument_id!r} already maps to different metadata"
                )
        provider_key = instrument.provider_key
        mapped_id = self._by_provider.get(provider_key)
        if mapped_id is not None and mapped_id != instrument.instrument_id:
            raise InstrumentConflictError(
                f"provider symbol {provider_key!r} already maps to {mapped_id!r}"
            )
        if existing is not None:
            self._by_provider.pop(existing.provider_key, None)
        self._by_id[instrument.instrument_id] = instrument
        self._by_provider[provider_key] = instrument.instrument_id
        return instrument

    def get(self, instrument_id: str) -> Instrument | None:
        return self._by_id.get(instrument_id)

    def require(self, instrument_id: str) -> Instrument:
        instrument = self.get(instrument_id)
        if instrument is None:
            raise KeyError(f"unknown instrument_id: {instrument_id!r}")
        return instrument

    def by_provider(self, provider_id: str, provider_symbol: str) -> Instrument | None:
        instrument_id = self._by_provider.get((provider_id, provider_symbol))
        return self._by_id.get(instrument_id) if instrument_id else None

    def by_asset_class(self, asset_class: AssetClass) -> tuple[Instrument, ...]:
        return tuple(
            instrument
            for instrument in self._by_id.values()
            if instrument.asset_class is asset_class
        )

    def all(self) -> tuple[Instrument, ...]:
        return tuple(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)

    def version_hash(self) -> str:
        """Stable hash of the whole registry, for experiment/dataset lineage."""
        payload = [instrument.to_dict() for instrument in sorted(
            self._by_id.values(), key=lambda item: item.instrument_id
        )]
        return sha256(_canonical_json({"instruments": payload}).encode("utf-8")).hexdigest()


def make_instrument_id(
    asset_class: AssetClass,
    venue: str,
    symbol: str,
    *,
    contract_type: ContractType = ContractType.SPOT,
    expiry: datetime | None = None,
    strike: float | None = None,
    option_type: str | None = None,
) -> str:
    """Build a deterministic canonical ID without silently merging contracts."""
    _require_text(venue, "venue")
    _require_text(symbol, "symbol")
    parts = [asset_class.value, venue, symbol]
    if contract_type is not ContractType.SPOT:
        parts.append(contract_type.value)
    if expiry is not None:
        _require_aware(expiry, "expiry")
        parts.append(expiry.astimezone(UTC).strftime("%Y%m%dT%H%MZ"))
    if strike is not None:
        parts.append(f"K{strike:g}")
    if option_type is not None:
        right = option_type.lower()
        if right not in _OPTION_RIGHTS:
            raise ValueError("option_type must be 'call' or 'put'")
        parts.append(right[0].upper())
    return ":".join(parts)


def instrument_symbol(instrument_id: str) -> str:
    """Extract the venue-local provider symbol from a canonical instrument ID.

    The canonical layout is ``<asset_class>:<venue>:<symbol>[:<contract>...]``.
    A provider must use this rather than the last colon-separated segment:
    an option ID ends with ``:C``/``:P`` and a perpetual ID ends with
    ``:perpetual``, neither of which is the provider symbol.
    """
    parts = instrument_id.split(":")
    if len(parts) < 3 or not parts[2].strip():
        raise ValueError(
            f"instrument_id {instrument_id!r} is not in "
            "'<asset_class>:<venue>:<symbol>[:...]' form"
        )
    return parts[2]


def instrument_venue(instrument_id: str) -> str:
    """Extract the venue from a canonical instrument ID."""
    parts = instrument_id.split(":")
    if len(parts) < 3 or not parts[1].strip():
        raise ValueError(
            f"instrument_id {instrument_id!r} is not in "
            "'<asset_class>:<venue>:<symbol>[:...]' form"
        )
    return parts[1]


__all__ = [
    "INSTRUMENT_SCHEMA_VERSION",
    "AssetClass",
    "ContractType",
    "Instrument",
    "InstrumentConflictError",
    "InstrumentRegistry",
    "instrument_symbol",
    "instrument_venue",
    "make_instrument_id",
]
