"""
Forex quantitative analytics — Phase 7b.

Pure, deterministic carry, currency-exposure and macro-event helpers.  No
I/O, no LLM, no look-ahead.  These functions model the economics; broker
rollover and financing data always take precedence over a policy-rate
approximation when it is available.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta


def _require_finite(value: float, name: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def carry_return(
    base_rate: float,
    quote_rate: float,
    holding_period_years: float,
    fx_return: float = 0.0,
    *,
    direction: int = 1,
) -> float:
    """Approximate carry plus FX return under a documented convention.

    A positive rate differential benefits the long base / short quote
    position when ``direction = +1``::

        R = direction * ((i_base - i_quote) * dt + FX_return)

    Set ``direction = -1`` for the opposite position.  The result is a
    decimal return (0.05 = 5%), not a percentage.

    This is a policy-rate approximation.  When the broker publishes actual
    rollover/financing charges, use :func:`broker_carry` and
    :func:`net_carry` instead of assuming the policy-rate differential is
    the financing cost.
    """
    if direction not in (-1, 1):
        raise ValueError(f"direction must be +1 or -1, got {direction}")
    _require_finite(base_rate, "base_rate")
    _require_finite(quote_rate, "quote_rate")
    _require_finite(holding_period_years, "holding_period_years")
    _require_finite(fx_return, "fx_return")
    if holding_period_years < 0:
        raise ValueError(
            f"holding_period_years must be non-negative, got {holding_period_years}"
        )
    rate_differential = (base_rate - quote_rate) * holding_period_years
    return direction * (rate_differential + fx_return)


def broker_carry(
    rollover_per_unit: float,
    units: float,
    *,
    direction: int = 1,
) -> float:
    """Carry from actual broker rollover data, in account currency.

    ``rollover_per_unit`` is the signed amount the broker credits or debits
    per unit of the position for the holding period.  ``units`` must be
    non-negative; ``direction`` applies the position sign.
    """
    if direction not in (-1, 1):
        raise ValueError(f"direction must be +1 or -1, got {direction}")
    _require_finite(rollover_per_unit, "rollover_per_unit")
    _require_finite(units, "units")
    if units < 0:
        raise ValueError(f"units must be non-negative, got {units}")
    return direction * rollover_per_unit * units


def net_carry(
    model_carry: float,
    *,
    broker_rollover: float | None = None,
    financing_cost: float = 0.0,
    spread_cost: float = 0.0,
) -> float:
    """Net carry with broker data taking precedence over the model.

    If ``broker_rollover`` is supplied it replaces ``model_carry``.  Costs
    are subtracted because ``financing_cost`` and ``spread_cost`` are
    expressed as positive amounts.
    """
    _require_finite(model_carry, "model_carry")
    _require_finite(financing_cost, "financing_cost")
    _require_finite(spread_cost, "spread_cost")
    if financing_cost < 0 or spread_cost < 0:
        raise ValueError("financing_cost and spread_cost must be non-negative")
    if broker_rollover is not None:
        _require_finite(broker_rollover, "broker_rollover")
        gross = broker_rollover
    else:
        gross = model_carry
    return gross - financing_cost - spread_cost


@dataclass(frozen=True)
class PositionExposure:
    """One position's contribution to currency exposure.

    ``value`` is the signed position value in the account's reporting
    currency.  ``exposures`` maps each currency to its signed exposure
    fraction or notional multiplier for that position.
    """

    position_id: str
    value: float
    exposures: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_finite(self.value, "value")
        for currency, exposure in self.exposures.items():
            if not currency:
                raise ValueError("currency codes must be non-empty")
            _require_finite(exposure, f"exposure[{currency}]")


def currency_exposure(
    positions: Iterable[PositionExposure],
) -> dict[str, float]:
    """Aggregate currency exposure across all instruments.

    Implements the specification formula::

        CurrencyExposure_c = sum_i(position_value_i * exposure_i,c)

    The result is sorted by currency code so callers can compare snapshots
    deterministically.
    """
    totals: dict[str, float] = defaultdict(float)
    for position in positions:
        for currency, exposure in position.exposures.items():
            totals[currency] += position.value * exposure
    return {currency: totals[currency] for currency in sorted(totals)}


@dataclass(frozen=True)
class MacroEvent:
    """Point-in-time economic-calendar event.

    ``event_time`` is when the event occurs; ``published_at`` is when the
    values became available.  Only events with ``published_at`` at or before
    the decision time may be used.  ``actual`` must be ``None`` until the
    value has actually been published.
    """

    event_time: datetime
    currency: str
    category: str
    previous: float | None
    forecast: float | None
    actual: float | None
    published_at: datetime
    source: str
    importance: int = 1

    def __post_init__(self) -> None:
        _require_aware(self.event_time, "event_time")
        _require_aware(self.published_at, "published_at")
        if not self.currency:
            raise ValueError("currency must be non-empty")
        if not self.category:
            raise ValueError("category must be non-empty")
        if self.importance < 1:
            raise ValueError(f"importance must be >= 1, got {self.importance}")
        for name, value in (
            ("previous", self.previous),
            ("forecast", self.forecast),
            ("actual", self.actual),
        ):
            if value is not None:
                _require_finite(value, name)


def visible_events(
    events: Sequence[MacroEvent],
    decision_time: datetime,
) -> tuple[MacroEvent, ...]:
    """Return events whose values were published by ``decision_time``.

    Timestamps are preserved unchanged; ordering is deterministic by
    ``(event_time, currency, category)``.
    """
    _require_aware(decision_time, "decision_time")
    visible = [event for event in events if event.published_at <= decision_time]
    visible.sort(key=lambda event: (event.event_time, event.currency, event.category))
    return tuple(visible)


def upcoming_events(
    events: Sequence[MacroEvent],
    decision_time: datetime,
    horizon: timedelta,
    *,
    currency: str | None = None,
    min_importance: int = 1,
) -> tuple[MacroEvent, ...]:
    """Events scheduled within ``[decision_time, decision_time + horizon]``."""
    _require_aware(decision_time, "decision_time")
    if horizon < timedelta(0):
        raise ValueError("horizon must be non-negative")
    if min_importance < 1:
        raise ValueError("min_importance must be >= 1")
    end = decision_time + horizon
    selected = [
        event
        for event in events
        if decision_time <= event.event_time <= end
        and event.importance >= min_importance
        and (currency is None or event.currency == currency)
    ]
    selected.sort(key=lambda event: (event.event_time, event.currency, event.category))
    return tuple(selected)


def in_macro_blackout(
    events: Sequence[MacroEvent],
    decision_time: datetime,
    *,
    currency: str,
    before: timedelta,
    after: timedelta,
    min_importance: int = 1,
) -> bool:
    """True when a material event for ``currency`` is inside the window."""
    _require_aware(decision_time, "decision_time")
    if before < timedelta(0) or after < timedelta(0):
        raise ValueError("before and after must be non-negative")
    if min_importance < 1:
        raise ValueError("min_importance must be >= 1")
    for event in events:
        if event.currency != currency or event.importance < min_importance:
            continue
        if event.event_time - before <= decision_time <= event.event_time + after:
            return True
    return False


__all__ = [
    "MacroEvent",
    "PositionExposure",
    "broker_carry",
    "carry_return",
    "currency_exposure",
    "in_macro_blackout",
    "net_carry",
    "upcoming_events",
    "visible_events",
]
