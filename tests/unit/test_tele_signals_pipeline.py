"""End-to-end tests for the Telegram signal pipeline (classify -> parse -> record -> risk gate)."""

import pytest

from axel.db.models import SignalModel
from axel.signals.classify import SignalKind
from axel.signals.pipeline import TeleSignalPipeline
from axel.signals.source import TelegramMessage


def _count_signals(session) -> int:
    return session.query(SignalModel).count()


def test_bet_signal_recorded_under_predictions(in_memory_db):
    pipeline = TeleSignalPipeline(session=in_memory_db)
    result = pipeline.ingest(TelegramMessage(chat="@betting", text="Booking Code: 4XK2Q9 win odds 1.85"))

    assert result.kind == SignalKind.BET_SIGNAL
    assert result.persisted
    assert result.parsed is not None
    assert result.parsed.code == "4XK2Q9"
    assert "daily allocation cap" in result.reason

    row = in_memory_db.query(SignalModel).filter_by(symbol="BET:4XK2Q9").one()
    assert row.section == "predictions"


def test_full_crypto_signal_runs_through_risk_engine(in_memory_db):
    pipeline = TeleSignalPipeline(session=in_memory_db)
    result = pipeline.ingest_text(
        "BUY BTCUSDT entry 63000 stop loss 61800 take profit 64500",
        chat="@crypto",
    )

    assert result.kind == SignalKind.TRADING_SIGNAL
    assert result.persisted
    assert result.proposal is not None
    assert result.proposal.section.value == "crypto"
    assert result.decision is not None
    assert result.proposal.symbol == "BTCUSDT"
    assert "risk engine" in result.reason

    assert _count_signals(in_memory_db) == 1
    assert in_memory_db.query(SignalModel).filter_by(symbol="BTCUSDT").one().section == "crypto"


def test_incomplete_levels_recorded_not_proposed(in_memory_db):
    pipeline = TeleSignalPipeline(session=in_memory_db)
    result = pipeline.ingest_text("BUY SOLUSDT entry 145", chat="@crypto")

    assert result.kind == SignalKind.TRADING_SIGNAL
    assert result.persisted
    assert result.proposal is None
    assert "incomplete or invalid" in result.reason
    assert _count_signals(in_memory_db) == 1


def test_low_confidence_trade_recorded_not_proposed(in_memory_db):
    pipeline = TeleSignalPipeline(session=in_memory_db, min_trade_confidence=0.95)
    result = pipeline.ingest_text("BUY BTCUSDT entry 63000 sl 61800 tp 64500", chat="@crypto")

    assert result.persisted
    assert result.proposal is None
    assert "below min trade confidence" in result.reason


def test_forex_signal_recorded_not_proposed(in_memory_db):
    pipeline = TeleSignalPipeline(session=in_memory_db)
    result = pipeline.ingest_text("EURUSD buy entry 1.0850 target 1.0920 stop 1.0810", chat="@forex")

    assert result.kind == SignalKind.TRADING_SIGNAL
    assert result.persisted
    assert result.proposal is None
    assert "forex" in result.reason
    assert in_memory_db.query(SignalModel).filter_by(symbol="EURUSD").one().section in ("stocks", "crypto")


def test_noise_is_dropped_without_persistence(in_memory_db):
    pipeline = TeleSignalPipeline(session=in_memory_db)
    result = pipeline.ingest(TelegramMessage(chat="@casual", text="gm everyone, how is it going?"))

    assert result.kind == SignalKind.NOISE
    assert not result.persisted
    assert _count_signals(in_memory_db) == 0


@pytest.mark.parametrize(
    "text",
    [
        "Booking Code: 4XK2Q9 win",
        "ACCUMULATOR: E7MX2A + code 9FHW33 sure banker",
        "SHORT ETHUSDT entry 3400 sl 3500 tp 3200 scalp",
        "$AAPL buy entry 185 target 200 stop 178.5 breakout",
    ],
)
def test_demo_messages_survive_the_pipeline(in_memory_db, text):
    pipeline = TeleSignalPipeline(session=in_memory_db)
    result = pipeline.ingest_text(text, chat="@demo")
    assert result.kind in (SignalKind.BET_SIGNAL, SignalKind.TRADING_SIGNAL)
    assert result.persisted