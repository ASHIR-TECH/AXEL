"""
Deterministic signal-quality scoring for untrusted Telegram signals.

Adapted from the heuristic scorer in AI-Trader
(https://github.com/TauricResearch/AI-Trader, service/server/signal_quality.py),
rewritten as a pure function with no database access.

A quality score is *advisory metadata only*: it never places an order, never
mutates a Proposal, and never bypasses the deterministic risk gates. Callers
may attach the result to a ``Signal`` record for later review.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256

from axel.core.types import OrderSide
from axel.signals.parsers import BetSignal, TradeSignal

MODEL_VERSION = "axel-heuristic-v1"
DEFAULT_SOURCE_TRUST = 0.5
_UNKNOWN_SOURCE_TRUST = 0.2
_TRUSTED_SOURCE_TRUST = 0.9

# Weighting mirrors the reference scorer: verifiability first, then evidence,
# specificity, novelty and reviewer/source trust.
_WEIGHTS = {
    "completeness": 0.30,
    "specificity": 0.20,
    "risk_reward": 0.25,
    "novelty": 0.15,
    "source_trust": 0.10,
}

_EVIDENCE_KEYWORDS = ("because", "risk", "evidence", "data", "chart", "catalyst")
_NUMBER_PATTERN = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class SignalQuality:
    """Advisory quality breakdown for one parsed signal (all sub-scores in [0, 1])."""

    completeness: float
    specificity: float
    risk_reward: float
    novelty: float
    source_trust: float
    overall: float
    reasons: tuple[str, ...]
    model_version: str = MODEL_VERSION


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def content_fingerprint(text: str | None) -> str:
    """Stable fingerprint of normalized message text, for duplicate detection."""
    normalized = " ".join((text or "").lower().split())
    return sha256(normalized.encode("utf-8")).hexdigest()


def _novelty(fingerprint: str, seen_fingerprints: Iterable[str]) -> tuple[float, tuple[str, ...]]:
    if fingerprint in set(seen_fingerprints):
        return 0.0, ("duplicate of an earlier message",)
    return 1.0, ()


def _source_trust(source: str, trusted_sources: Iterable[str]) -> tuple[float, tuple[str, ...]]:
    normalized = (source or "").strip().lower()
    if not normalized or normalized == "unknown":
        return _UNKNOWN_SOURCE_TRUST, ("unknown source",)
    if normalized in {item.strip().lower() for item in trusted_sources}:
        return _TRUSTED_SOURCE_TRUST, ("configured trusted source",)
    return DEFAULT_SOURCE_TRUST, ()


def _specificity(text: str) -> tuple[float, tuple[str, ...]]:
    """Reward messages that state concrete numbers and reasons rather than vibes."""
    numbers = min(len(_NUMBER_PATTERN.findall(text or "")), 4)
    keywords = sum(1 for word in _EVIDENCE_KEYWORDS if word in (text or "").lower())
    score = min(numbers / 4.0, 1.0) * 0.6 + min(keywords, 3) / 3.0 * 0.4
    reasons = []
    if numbers:
        reasons.append(f"{numbers} numeric level(s)")
    if keywords:
        reasons.append(f"{keywords} evidence keyword(s)")
    if not reasons:
        reasons.append("no numeric levels or evidence keywords")
    return _clamp(score), tuple(reasons)


def _risk_reward(signal: TradeSignal) -> tuple[float, tuple[str, ...]]:
    """Score reward-to-risk geometry; malformed geometry scores zero, never raises."""
    if signal.side is None:
        return 0.0, ("side is missing",)
    if signal.entry is None or signal.stop is None or signal.target is None:
        return 0.0, ("entry/stop/target is incomplete",)

    if signal.side is OrderSide.BUY:
        risk = signal.entry - signal.stop
        reward = signal.target - signal.entry
    else:
        risk = signal.stop - signal.entry
        reward = signal.entry - signal.target

    if risk <= 0 or reward <= 0:
        return 0.0, ("non-positive risk/reward geometry",)

    ratio = reward / risk
    return _clamp(ratio / 3.0), (f"reward/risk {ratio:.2f}:1",)


def _overall(**scores: float) -> float:
    return _clamp(sum(scores[name] * weight for name, weight in _WEIGHTS.items()))


def score_trade_signal(
    signal: TradeSignal,
    *,
    seen_fingerprints: Iterable[str] = (),
    trusted_sources: Iterable[str] = (),
) -> SignalQuality:
    """Score one parsed trade signal deterministically (pure, read-only)."""
    completeness = _clamp(
        sum(
            (
                signal.symbol != "",
                signal.side is not None,
                signal.entry is not None,
                signal.stop is not None,
                signal.target is not None,
            )
        )
        / 5.0
    )
    specificity, specificity_reasons = _specificity(signal.raw_text)
    risk_reward, risk_reasons = _risk_reward(signal)
    novelty, novelty_reasons = _novelty(content_fingerprint(signal.raw_text), seen_fingerprints)
    source_trust, source_reasons = _source_trust(signal.source, trusted_sources)

    reasons = list(specificity_reasons) + list(risk_reasons) + list(novelty_reasons) + list(source_reasons)
    if completeness < 1.0:
        reasons.append("incomplete trade levels")

    scores = {
        "completeness": completeness,
        "specificity": specificity,
        "risk_reward": risk_reward,
        "novelty": novelty,
        "source_trust": source_trust,
    }
    return SignalQuality(overall=_overall(**scores), reasons=tuple(reasons), **scores)


def score_bet_signal(
    signal: BetSignal,
    *,
    seen_fingerprints: Iterable[str] = (),
    trusted_sources: Iterable[str] = (),
) -> SignalQuality:
    """Score one parsed betting signal deterministically (pure, read-only)."""
    completeness = _clamp((signal.code != "") / 2.0 + (bool(signal.raw_text.strip())) / 2.0)
    specificity, specificity_reasons = _specificity(signal.raw_text)
    novelty, novelty_reasons = _novelty(content_fingerprint(signal.raw_text), seen_fingerprints)
    source_trust, source_reasons = _source_trust(signal.source, trusted_sources)

    reasons = list(specificity_reasons) + list(novelty_reasons) + list(source_reasons)
    if completeness < 1.0:
        reasons.append("incomplete booking code")

    scores = {
        "completeness": completeness,
        # Betting slips carry no price geometry, so the parse confidence is the
        # only deterministic substitute for a risk/reward read.
        "specificity": specificity,
        "risk_reward": _clamp(signal.confidence),
        "novelty": novelty,
        "source_trust": source_trust,
    }
    return SignalQuality(overall=_overall(**scores), reasons=tuple(reasons), **scores)


def score_signal(
    signal: TradeSignal | BetSignal,
    *,
    seen_fingerprints: Iterable[str] = (),
    trusted_sources: Iterable[str] = (),
) -> SignalQuality:
    """Dispatch to the matching scorer for a parsed signal."""
    if isinstance(signal, TradeSignal):
        return score_trade_signal(
            signal, seen_fingerprints=seen_fingerprints, trusted_sources=trusted_sources
        )
    if isinstance(signal, BetSignal):
        return score_bet_signal(
            signal, seen_fingerprints=seen_fingerprints, trusted_sources=trusted_sources
        )
    raise TypeError(f"unsupported signal type: {type(signal).__name__}")
