"""Tests for the Telegram signal parsers (bet codes + financial trade signals)."""

from axel.core.types import OrderSide
from axel.signals.parsers import parse_bet_signal, parse_trading_signal


class TestBetParser:
    def test_labeled_booking_code(self):
        parsed = parse_bet_signal("Booking Code: 4XK2Q9 win odds 1.85")
        assert parsed is not None
        assert parsed.code == "4XK2Q9"

    def test_hash_prefixed_code(self):
        parsed = parse_bet_signal("sure banker today #9FHW33")
        assert parsed is not None
        assert parsed.code == "9FHW33"

    def test_standalone_code_line(self):
        parsed = parse_bet_signal("E7MX2A")
        assert parsed is not None
        assert parsed.code == "E7MX2A"

    def test_ignore_words_are_not_codes(self):
        assert parse_bet_signal("WIN BET TODAY") is None

    def test_codes_require_letter_and_digit(self):
        assert parse_bet_signal("ABCDEF") is None
        assert parse_bet_signal("123456") is None

    def test_no_code_returns_none(self):
        assert parse_bet_signal("join our vip group") is None


class TestTradeParser:
    def test_crypto_pair(self):
        parsed = parse_trading_signal(
            "BUY BTCUSDT entry 63000 stop loss 61800 take profit 64500"
        )
        assert parsed is not None
        assert parsed.symbol == "BTCUSDT"
        assert parsed.market == "crypto"
        assert parsed.side == OrderSide.BUY
        assert parsed.entry == 63000.0
        assert parsed.stop == 61800.0
        assert parsed.target == 64500.0
        assert parsed.confidence >= 0.5

    def test_crypto_short_with_sl_tp(self):
        parsed = parse_trading_signal("SHORT ETHUSDT entry 3400 sl 3500 tp 3200 scalp")
        assert parsed is not None
        assert parsed.symbol == "ETHUSDT"
        assert parsed.side == OrderSide.SELL
        assert parsed.stop == 3500.0
        assert parsed.target == 3200.0

    def test_forex_pair(self):
        parsed = parse_trading_signal("EURUSD buy entry 1.0850 target 1.0920 stop 1.0810")
        assert parsed is not None
        assert parsed.market == "forex"
        assert parsed.entry == 1.0850

    def test_dollar_ticker_stock(self):
        parsed = parse_trading_signal("$AAPL buy entry 185 target 200 stop 178.5")
        assert parsed is not None
        assert parsed.symbol == "AAPL"
        assert parsed.market == "stocks"

    def test_missing_symbol_is_none(self):
        assert parse_trading_signal("nice little scalp today") is None

    def test_entry_only_records_levels(self):
        parsed = parse_trading_signal("BUY SOLUSDT entry 145")
        assert parsed is not None
        assert parsed.entry == 145.0
        assert parsed.stop is None
        assert parsed.target is None