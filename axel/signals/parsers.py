"""
Deterministic parsers for untrusted Telegram signal text.

Booking-code extraction is adapted from the SportyClaw-Autoplacer project
(https://github.com/Contractor-x/SportyClaw-Autoplacer, bot/parser.py) and
generalized so AXEL can parse both betting codes and financial trade signals.

Telegram text is DATA. It is parsed here, then passed through the same
deterministic risk gates as any other signal source.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from axel.core.types import OrderSide

BET_IGNORE_WORDS = {
    "THE",
    "AND",
    "FOR",
    "WIN",
    "BET",
    "ODD",
    "ODDS",
    "HIGH",
    "LOW",
    "TOP",
    "HOT",
    "TODAY",
    "STAKE",
    "STAKES",
    "MATCH",
    "MATCHES",
    "BOOKING",
    "CODE",
    "SLIP",
    "SPORTY",
    "SPORTYBET",
    "TOTAL",
    "SINGLE",
    "MULTIPLE",
    "SYSTEM",
    "HOME",
    "AWAY",
    "OVER",
    "UNDER",
    "PRAY",
}

CRYPTO_SPOTS = {
    "BTC",
    "ETH",
    "XRP",
    "SOL",
    "ADA",
    "DOGE",
    "BNB",
    "DOT",
    "LTC",
    "AVAX",
    "MATIC",
    "POL",
    "LINK",
    "SHIB",
    "PEPE",
    "TON",
    "APT",
    "ARB",
    "OP",
    "NEAR",
    "SUI",
    "INJ",
    "ATOM",
}

FOREX_PAIRS = {
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCHF",
    "USDCAD",
    "NZDUSD",
    "EURJPY",
    "GBPJPY",
    "EURGBP",
    "EURAUD",
    "GBPAUD",
}

Market = Literal["crypto", "forex", "stocks"]

_CRYPTO_PAIR_PATTERN = re.compile(
    rf"\b({'|'.join(sorted(CRYPTO_SPOTS))})(?:\s?/\s?)?(USDT|USDC|USD|BTC)?(?:USDT|USDC)?\b",
    re.IGNORECASE,
)
_FOREX_PAIR_PATTERN = re.compile(rf"\b({'|'.join(sorted(FOREX_PAIRS))})\b", re.IGNORECASE)
_DOLLAR_TICKER_PATTERN = re.compile(r"\$([A-Z]{1,5})\b", re.IGNORECASE)


def _is_valid_bet_code(code: str) -> bool:
    candidate = code.strip().upper()
    if len(candidate) != 6:
        return False
    if candidate in BET_IGNORE_WORDS:
        return False
    if not re.fullmatch(r"[A-Z0-9]+", candidate):
        return False
    return bool(re.search(r"[A-Z]", candidate) and re.search(r"\d", candidate))


def iter_bet_codes(text: str | None) -> list[str]:
    """Extract 6-char booking codes from message text (generalized SportyClaw parser)."""
    if not text:
        return []
    raw = text.strip().upper()
    found: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        code = candidate.strip().upper()
        if code and code not in seen and _is_valid_bet_code(code):
            seen.add(code)
            found.append(code)

    labeled_matches = [
        re.search(r"\bBooking(?:\s*Code)?\s*[:\-]?\s*([A-Z0-9]{6})\b", raw),
        re.search(r"\bBet\s*Code\s*[:\-]?\s*([A-Z0-9]{6})\b", raw),
        re.search(r"\bCode\s*[:\-]?\s*([A-Z0-9]{6})\b", raw),
        re.search(r"\bSlip\s*[:\-]?\s*([A-Z0-9]{6})\b", raw),
        re.search(r"#([A-Z0-9]{6})\b", raw),
    ]
    for match in labeled_matches:
        if match:
            _add(match.group(1))

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[A-Z0-9]{6}", line):
            _add(line)
    for token in re.findall(r"\b[A-Z0-9]{6}\b", raw):
        _add(token)
    return found


def first_bet_code(text: str | None) -> str | None:
    codes = iter_bet_codes(text)
    return codes[0] if codes else None


@dataclass(frozen=True)
class BetSignal:
    """Normalized betting signal (booking code) extracted from a Telegram message."""

    code: str
    source: str
    raw_text: str
    confidence: float
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_bet_signal(text: str | None, source: str = "unknown") -> BetSignal | None:
    codes = iter_bet_codes(text)
    if not codes:
        return None
    # Deterministic confidence heuristic: labeled/standalone codes score higher.
    raw = (text or "").upper()
    labels = sum(1 for marker in ("BOOKING", "BET CODE", "CODE:", "SLIP", "#") if marker in raw)
    confidence = min(0.6 + 0.05 * labels + 0.05 * min(len(codes), 3), 0.95)
    return BetSignal(code=codes[0], source=source, raw_text=text or "", confidence=round(confidence, 3))


def _detect_symbol(text: str) -> tuple[str, Market] | None:
    upper = text or ""
    match = re.search(r"\$([A-Z]{1,5})\b", upper, re.IGNORECASE)
    if match:
        return match.group(1).upper(), "stocks"

    for m in _CRYPTO_PAIR_PATTERN.finditer(upper):
        token = m.group(0).upper().replace("/", "")
        if any(token.endswith(quote) for quote in ("USDT", "USDC", "USD", "BTC")):
            return token, "crypto"
        if token in CRYPTO_SPOTS and m.group(2) is None:
            return token, "crypto"

    forex = _FOREX_PAIR_PATTERN.search(upper)
    if forex:
        return forex.group(1).upper(), "forex"

    return None


_SIDE_LONG = re.compile(r"\b(buy|long|go\s?long|call)\b", re.IGNORECASE)
_SIDE_SHORT = re.compile(r"\b(sell|short|go\s?short|put)\b", re.IGNORECASE)

_ENTRY_PATTERN = re.compile(
    r"\b(?:entry|entered|enter|in\s?at)\s*[:=@]?\s*([0-9][0-9.,]*)\b", re.IGNORECASE
)
_TP_PATTERN = re.compile(
    r"\b(?:tp|take\s?profit|takeprofit|targets?|t\s*1|tp1)\s*[:=]?\s*([0-9][0-9.,]*)\b", re.IGNORECASE
)
_SL_PATTERN = re.compile(
    r"\b(?:sl|stop\s?loss|stoploss|stop)\s*[:=]?\s*([0-9][0-9.,]*)\b", re.IGNORECASE
)


def _to_float(raw: str) -> float | None:
    value = raw.strip().replace(" ", "")
    if not value:
        return None
    try:
        if "," in value and "." in value:
            value = value.replace(",", "")
        elif "," in value:
            value = value.replace(",", ".")
        return round(float(value), 6)
    except ValueError:
        return None


def _first_match(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    return _to_float(match.group(1)) if match else None


@dataclass(frozen=True)
class TradeSignal:
    """Normalized financial trade signal extracted from a Telegram message."""

    symbol: str
    market: Market
    source: str
    raw_text: str
    side: OrderSide | None
    entry: float | None
    stop: float | None
    target: float | None
    confidence: float
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_trading_signal(text: str | None, source: str = "unknown") -> TradeSignal | None:
    if not text or not text.strip():
        return None

    detected = _detect_symbol(text)
    if detected is None:
        return None
    symbol, market = detected

    side: OrderSide | None = None
    if _SIDE_LONG.search(text):
        side = OrderSide.BUY
    elif _SIDE_SHORT.search(text):
        side = OrderSide.SELL

    entry = _first_match(_ENTRY_PATTERN, text)
    target = _first_match(_TP_PATTERN, text)
    stop = _first_match(_SL_PATTERN, text)

    confidence = 0.45
    if symbol:
        confidence += 0.15
    if side:
        confidence += 0.10
    if entry:
        confidence += 0.10
    if stop and target:
        confidence += 0.10
    confidence = min(confidence, 0.95)

    return TradeSignal(
        symbol=symbol,
        market=market,
        source=source,
        raw_text=text,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        confidence=round(confidence, 3),
    )