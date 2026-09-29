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

__all__ = [
    "AllocationBankroll",
    "AllocationResult",
    "BetSignal",
    "IngestedSignal",
    "SignalKind",
    "SignalsConfig",
    "TeleSignalPipeline",
    "TradeSignal",
    "classify_message",
    "parse_bet_signal",
    "parse_trading_signal",
]