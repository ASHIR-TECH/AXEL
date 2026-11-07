"""Tests for deterministic, advisory-only signal quality scoring."""

from datetime import UTC, datetime

import pytest

from axel.core.types import OrderSide
from axel.signals.parsers import BetSignal, TradeSignal
from axel.signals.quality import (
    MODEL_VERSION,
    SignalQuality,
    content_fingerprint,
    score_bet_signal,
    score_signal,
    score_trade_signal,
)

_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _trade(
    *,
    side: OrderSide | None = OrderSide.BUY,
    entry: float | None = 100.0,
    stop: float | None = 95.0,
    target: float | None = 110.0,
    text: str = "Buy BTC entry 100 stop 95 target 110 because breakout data",
    source: str = "alpha",
) -> TradeSignal:
    return TradeSignal(
        symbol="BTCUSDT",
        market="crypto",
        source=source,
        raw_text=text,
        side=side,
        entry=entry,
        stop=stop,
        target=target,
        confidence=0.85,
        ts=_TS,
    )


def _assert_unit_interval(quality: SignalQuality) -> None:
    for value in (
        quality.completeness,
        quality.specificity,
        quality.risk_reward,
        quality.novelty,
        quality.source_trust,
        quality.overall,
    ):
        assert 0.0 <= value <= 1.0


def test_complete_signal_scores_high() -> None:
    quality = score_trade_signal(_trade(), trusted_sources=("alpha",))

    _assert_unit_interval(quality)
    assert quality.completeness == 1.0
    assert quality.risk_reward == pytest.approx(2 / 3, abs=1e-4)
    assert quality.novelty == 1.0
    assert quality.source_trust == 0.9
    assert quality.overall > 0.7
    assert "reward/risk 2.00:1" in quality.reasons
    assert quality.model_version == MODEL_VERSION


def test_missing_levels_reduce_completeness_and_zero_risk_reward() -> None:
    quality = score_trade_signal(_trade(stop=None))

    assert quality.completeness == pytest.approx(0.8)
    assert quality.risk_reward == 0.0
    assert "entry/stop/target is incomplete" in quality.reasons


def test_missing_side_cannot_score_geometry() -> None:
    quality = score_trade_signal(_trade(side=None))

    assert quality.completeness == pytest.approx(0.8)
    assert quality.risk_reward == 0.0
    assert "side is missing" in quality.reasons


def test_short_geometry_is_scored_mirrorwise() -> None:
    quality = score_trade_signal(
        _trade(side=OrderSide.SELL, entry=100.0, stop=105.0, target=90.0)
    )

    assert quality.risk_reward == pytest.approx(2 / 3, abs=1e-4)
    assert "reward/risk 2.00:1" in quality.reasons


def test_inverted_geometry_scores_zero_instead_of_raising() -> None:
    quality = score_trade_signal(_trade(entry=100.0, stop=105.0, target=110.0))

    assert quality.risk_reward == 0.0
    assert "non-positive risk/reward geometry" in quality.reasons


def test_duplicate_content_loses_novelty() -> None:
    signal = _trade()
    fingerprint = content_fingerprint(signal.raw_text)

    fresh = score_trade_signal(signal)
    duplicate = score_trade_signal(signal, seen_fingerprints=(fingerprint,))

    assert fresh.novelty == 1.0
    assert duplicate.novelty == 0.0
    assert duplicate.overall < fresh.overall
    assert "duplicate of an earlier message" in duplicate.reasons


def test_fingerprint_ignores_case_and_whitespace() -> None:
    assert content_fingerprint("  BUY   BTC ") == content_fingerprint("buy btc")


def test_unknown_source_is_penalized() -> None:
    quality = score_trade_signal(_trade(source="unknown"))

    assert quality.source_trust == 0.2
    assert "unknown source" in quality.reasons


def test_bet_signal_scores_with_confidence_as_geometry_substitute() -> None:
    bet = BetSignal(
        code="ABC123",
        source="slip",
        raw_text="Booking code ABC123 stake 2 odds 1.5",
        confidence=0.8,
        ts=_TS,
    )

    quality = score_bet_signal(bet, trusted_sources=("slip",))

    _assert_unit_interval(quality)
    assert quality.completeness == 1.0
    assert quality.risk_reward == pytest.approx(0.8)
    assert quality.source_trust == 0.9
    assert quality.overall > 0.5


def test_dispatch_selects_matching_scorer() -> None:
    trade = _trade()
    bet = BetSignal(code="ABC123", source="slip", raw_text="ABC123", confidence=0.6, ts=_TS)

    assert score_signal(trade) == score_trade_signal(trade)
    assert score_signal(bet) == score_bet_signal(bet)
    with pytest.raises(TypeError):
        score_signal(object())  # type: ignore[arg-type]


def test_scoring_is_pure_and_repeatable() -> None:
    signal = _trade()
    before = (signal.raw_text, signal.source, signal.entry, signal.stop, signal.target)

    first = score_trade_signal(signal)
    second = score_trade_signal(signal)

    assert first == second
    assert (signal.raw_text, signal.source, signal.entry, signal.stop, signal.target) == before
