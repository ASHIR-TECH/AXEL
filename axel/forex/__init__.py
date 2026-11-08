"""
Forex analytics package — Phase 7b.

Pure carry, currency-exposure and macro-event helpers.  The package has no
broker, execution or LLM imports; it produces inputs for the deterministic
risk and portfolio layers only.
"""

from axel.forex.analytics import (
    MacroEvent,
    PositionExposure,
    broker_carry,
    carry_return,
    currency_exposure,
    in_macro_blackout,
    net_carry,
    upcoming_events,
    visible_events,
)

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
