"""
Classify raw Telegram text into signal categories (bet, trading, or noise).

Telegram text is UNTRUSTED DATA. Classification is deterministic and purely
keyword/regex based; nothing here can cause an order to be placed.
"""

from enum import Enum

from axel.signals.parsers import iter_bet_codes


class SignalKind(str, Enum):
    BET_SIGNAL = "bet_signal"
    TRADING_SIGNAL = "trading_signal"
    NOISE = "noise"


_BET_KEYWORDS = (
    "booking",
    "bet code",
    "slip",
    "odds",
    "stake",
    "sporty",
    "sportybet",
    "accumulator",
    "single bet",
    "sure banker",
    "gentleman",
)

_TRADING_KEYWORDS = (
    "buy",
    "sell",
    "long",
    "short",
    "entry",
    "entered",
    "take profit",
    "takeprofit",
    "stop loss",
    "stoploss",
    " target",
    "tp ",
    "tp1",
    " sl ",
    "sl:",
    " leverage",
    "signal",
    "chart",
    "support",
    "resistance",
    "breakout",
    " spot ",
    "future",
    "indicator",
)


def _keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for keyword in keywords if keyword in lowered)


def classify_message(text: str | None, source_hint: str = "") -> SignalKind:
    """Return the signal kind for a raw Telegram message, or NOISE."""
    if not text or not text.strip():
        return SignalKind.NOISE

    if iter_bet_codes(text):
        return SignalKind.BET_SIGNAL

    bet_hits = _keyword_hits(text, _BET_KEYWORDS)
    trade_hits = _keyword_hits(text, _TRADING_KEYWORDS)

    if bet_hits >= 2 and bet_hits > trade_hits:
        return SignalKind.BET_SIGNAL
    if trade_hits >= 2:
        return SignalKind.TRADING_SIGNAL
    if bet_hits >= 2:
        return SignalKind.BET_SIGNAL
    return SignalKind.NOISE