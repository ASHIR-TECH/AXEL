"""Tests for Telegram message classification."""

from axel.signals.classify import SignalKind, classify_message


def test_empty_text_is_noise():
    assert classify_message(None) == SignalKind.NOISE
    assert classify_message("") == SignalKind.NOISE
    assert classify_message("   ") == SignalKind.NOISE


def test_booking_code_is_bet_signal():
    assert classify_message("Booking Code: 4XK2Q9 win") == SignalKind.BET_SIGNAL


def test_betting_keywords_are_bet_signal():
    assert classify_message("sure banker, accumulator slip odds 2.4") == SignalKind.BET_SIGNAL


def test_trading_keywords_are_trading_signal():
    assert classify_message("BUY BTCUSDT entry 63000 take profit 64500") == SignalKind.TRADING_SIGNAL


def test_casual_chat_is_noise():
    assert classify_message("gm everyone, how is the market today?") == SignalKind.NOISE
    assert classify_message("join our vip group for winners") == SignalKind.NOISE