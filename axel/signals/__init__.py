"""
AXEL Telegram signal ingestion.

Telegram group text is treated as UNTRUSTED DATA: it is classified, parsed,
and only then fed through the same deterministic risk gates as every other
signal source. No Telegram message can ever issue an order directly.
"""

from axel.signals.bankroll import AllocationBankroll, AllocationResult
from axel.signals.classify import SignalKind, classify_message
from axel.signals.config import SignalsConfig
from axel.signals.parsers import BetSignal, TradeSignal, parse_bet_signal, parse_trading_signal
from axel.signals.pipeline import IngestedSignal, TeleSignalPipeline
from axel.signals.quality import (
    SignalQuality,
    content_fingerprint,
    score_bet_signal,
    score_signal,
    score_trade_signal,
)

__all__ = [
    "AllocationBankroll",
    "AllocationResult",
    "BetSignal",
    "IngestedSignal",
    "SignalKind",
    "SignalQuality",
    "SignalsConfig",
    "TeleSignalPipeline",
    "TradeSignal",
    "classify_message",
    "content_fingerprint",
    "parse_bet_signal",
    "parse_trading_signal",
    "score_bet_signal",
    "score_signal",
    "score_trade_signal",
]