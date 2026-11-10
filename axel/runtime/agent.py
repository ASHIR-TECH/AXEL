"""Agent worker: deterministic analyst signals and trade proposals.

Trust boundary
--------------
This module may only *propose*.  It never evaluates risk, sizes an order, or
touches execution state.  Proposals are persisted with ``status="pending"`` and
are consumed by :mod:`axel.runtime.core`, which runs the deterministic gate.

The "analysts" here are small, explainable indicator functions.  They exist so
the paper runtime can produce real, durable proposals without an LLM or external
API key; a live deployment can replace them with the LLM panel behind the same
contract.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from itertools import pairwise

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from axel.core.config import settings
from axel.core.contracts import Proposal
from axel.core.ids import generate_id
from axel.core.types import OrderSide, Section, TradingMode
from axel.db.models import (
    AgentRunModel,
    PanelDecisionModel,
    PositionModel,
    SignalModel,
    TradeProposalModel,
)
from axel.runtime import audit
from axel.runtime.paper_feed import latest_price, recent_closes
from axel.runtime.sections import trading_sections

ANALYSTS: tuple[str, ...] = (
    "TECHNICAL",
    "FUNDAMENTAL",
    "SENTIMENT",
    "MACRO_CARRY",
    "VOL_GREEKS",
    "ODDS_CALIB",
)

_MIN_STRENGTH = 0.35
_MIN_PROPOSAL_STRENGTH = 0.55
_STOP_PCT_FLOOR = 0.004
_STOP_PCT_CAP = 0.03
_TARGET_R = 2.0


def _returns(closes: list[float]) -> list[float]:
    return [
        (current / previous) - 1.0
        for previous, current in pairwise(closes)
        if previous
    ]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _direction(value: float) -> str:
    if value > 0:
        return "LONG"
    if value < 0:
        return "SHORT"
    return "NEUTRAL"


def _signal_for(analyst: str, section: str, closes: list[float]) -> tuple[str, float, str]:
    """Return ``(direction, strength, rationale)`` for one analyst/section."""
    if len(closes) < 8:
        return "NEUTRAL", 0.0, "insufficient history"

    last = closes[-1]
    momentum = (last / closes[-6]) - 1.0 if closes[-6] else 0.0
    short = (last / closes[-2]) - 1.0 if closes[-2] else 0.0
    sma = _mean(closes[-20:])
    value_gap = (sma - last) / sma if sma else 0.0
    vol = _stdev(_returns(closes[-20:]))

    if analyst == "TECHNICAL":
        strength = min(1.0, abs(momentum) / 0.02)
        return _direction(momentum), strength, f"5-bar momentum {momentum:+.2%}"

    if analyst == "FUNDAMENTAL":
        strength = min(1.0, abs(value_gap) / 0.03)
        return _direction(value_gap), strength, f"price vs SMA20 {value_gap:+.2%}"

    if analyst == "SENTIMENT":
        strength = min(1.0, abs(short) / 0.01)
        return _direction(-short), strength, f"1-bar reversal {short:+.2%}"

    if analyst == "MACRO_CARRY":
        # Forex carry is a slow, persistent signal; other sections use trend.
        if section == "FOREX":
            carry = (hash(f"carry:{section}") % 7 - 3) / 100.0
            strength = min(1.0, 0.45 + abs(carry) * 4.0)
            return _direction(carry), strength, f"policy-rate carry {carry:+.2%}"
        strength = min(1.0, abs(momentum) / 0.03)
        return _direction(momentum), strength, f"macro trend {momentum:+.2%}"

    if analyst == "VOL_GREEKS":
        z_score = (last - sma) / (sma * vol) if sma and vol > 0 else 0.0
        strength = min(1.0, abs(z_score) / 2.0)
        return _direction(-z_score), strength, f"vol-adjusted z-score {z_score:+.2f}"

    # ODDS_CALIB
    if section == "PREDICTIONS":
        edge = last - 0.5
        strength = min(1.0, abs(edge) / 0.15)
        return _direction(edge), strength, f"contract edge {edge:+.3f}"
    strength = min(1.0, abs(momentum) / (0.02 + vol))
    return _direction(momentum), strength, f"calibrated momentum {momentum:+.2%}"


def _latest_signal(
    session: Session, analyst: str, section: str
) -> SignalModel | None:
    stmt = (
        select(SignalModel)
        .where(SignalModel.analyst == analyst, SignalModel.section == section)
        .order_by(SignalModel.ts.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _should_write(
    latest: SignalModel | None, direction: str, now: datetime, ttl_seconds: int
) -> bool:
    if latest is None:
        return direction != "NEUTRAL"
    stamp = latest.ts if latest.ts.tzinfo else latest.ts.replace(tzinfo=UTC)
    age = (now - stamp).total_seconds()
    if direction == "NEUTRAL":
        return age >= ttl_seconds
    if str(latest.direction).upper() != direction:
        return True
    return age >= ttl_seconds / 3.0


def generate_signals(session: Session, *, now: datetime | None = None) -> int:
    """Write analyst signals that are new, changed, or expired."""
    stamp = now or datetime.now(UTC)
    ttl = settings.paper_runtime_signal_ttl_seconds
    written = 0

    for section, symbols in trading_sections().items():
        closes = recent_closes(session, symbols[0], 40)
        for analyst in ANALYSTS:
            direction, strength, rationale = _signal_for(analyst, section, closes)
            latest = _latest_signal(session, analyst, section)
            if not _should_write(latest, direction, stamp, ttl):
                continue
            session.add(
                SignalModel(
                    id=generate_id("sig"),
                    ts=stamp,
                    as_of=stamp,
                    section=section,
                    symbol=symbols[0],
                    analyst=analyst,
                    direction=direction,
                    strength=round(max(0.0, min(1.0, strength)), 4),
                    horizon="1d",
                    features_ref=rationale,
                )
            )
            written += 1
            if direction != "NEUTRAL" and strength >= 0.7:
                audit.record(
                    session,
                    event_type="analyst.signal",
                    actor="axel.agents.analyst",
                    message=(
                        f"{analyst.lower()} → SIGNAL {section}/{direction} "
                        f"strength={strength:.2f} · {rationale}"
                    ),
                    logger="axel.agents.analyst",
                    channel="ANALYSIS",
                    level="INFO",
                    timestamp=stamp,
                    section=section,
                    direction=direction,
                    strength=round(strength, 4),
                )
    return written


def _best_signal(session: Session, section: str, since: datetime) -> SignalModel | None:
    stmt = (
        select(SignalModel)
        .where(
            SignalModel.section == section,
            SignalModel.ts >= since,
            SignalModel.direction.in_(("LONG", "SHORT")),
        )
        .order_by(SignalModel.strength.desc(), SignalModel.ts.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _has_recent_proposal(session: Session, section: str, now: datetime, cooldown: int) -> bool:
    stmt = (
        select(func.count())
        .select_from(TradeProposalModel)
        .where(
            TradeProposalModel.section == section,
            TradeProposalModel.created_at >= now - timedelta(seconds=cooldown),
        )
    )
    return int(session.execute(stmt).scalar_one()) > 0


def _has_open_position(session: Session, symbol: str) -> bool:
    stmt = (
        select(func.count())
        .select_from(PositionModel)
        .where(PositionModel.symbol == symbol, PositionModel.qty > 0)
    )
    return int(session.execute(stmt).scalar_one()) > 0


def _stop_distance(entry: float, closes: list[float]) -> float:
    vol = _stdev(_returns(closes[-20:]))
    distance = max(entry * _STOP_PCT_FLOOR, entry * max(vol, 0.002) * 3.0)
    return min(distance, entry * _STOP_PCT_CAP)


def propose(session: Session, *, now: datetime | None = None) -> int:
    """Turn the strongest recent signal in each section into a pending proposal."""
    stamp = now or datetime.now(UTC)
    cooldown = settings.paper_runtime_proposal_cooldown_seconds
    window = timedelta(seconds=settings.paper_runtime_signal_ttl_seconds)
    created = 0

    for section, symbols in trading_sections().items():
        if _has_recent_proposal(session, section, stamp, cooldown):
            continue
        signal = _best_signal(session, section, stamp - window)
        if signal is None or float(signal.strength) < _MIN_PROPOSAL_STRENGTH:
            continue
        symbol = signal.symbol or symbols[0]
        if _has_open_position(session, symbol):
            continue
        entry = latest_price(session, symbol)
        if entry is None or entry <= 0:
            continue
        closes = recent_closes(session, symbol, 40)
        distance = _stop_distance(entry, closes)
        side = OrderSide.BUY if str(signal.direction).upper() == "LONG" else OrderSide.SELL
        stop = entry - distance if side is OrderSide.BUY else entry + distance
        target = entry + _TARGET_R * distance if side is OrderSide.BUY else entry - _TARGET_R * distance
        confidence = max(0.3, min(0.95, float(signal.strength)))
        win_rate = max(0.45, min(0.65, 0.5 + 0.15 * confidence))
        proposal = Proposal(
            section=Section(section.lower()),
            symbol=symbol,
            side=side,
            entry=round(entry, 6),
            stop=round(max(0.01, stop), 6),
            target=round(max(0.01, target), 6),
            strategy_id=f"{signal.analyst}_PAPER",
            panel_confidence=round(confidence, 4),
            win_rate=round(win_rate, 4),
            avg_win_loss_ratio=_TARGET_R,
            rationale_ref=f"{signal.analyst.lower()}:{signal.features_ref or 'indicator'}",
            mode=TradingMode.PAPER,
            ttl_seconds=300,
        )
        session.add(
            TradeProposalModel(
                id=proposal.id,
                section=section,
                symbol=proposal.symbol,
                side=proposal.side.value,
                entry_price=proposal.entry,
                stop_loss=proposal.stop,
                take_profit=proposal.target,
                r_multiple_target=_TARGET_R,
                position_size_usd=0.0,
                confidence_score=proposal.panel_confidence,
                strategy_id=proposal.strategy_id,
                mode="paper",
                status="pending",
                ttl_expires_at=stamp + timedelta(seconds=proposal.ttl_seconds),
                agent_reasoning=signal.features_ref,
                created_at=stamp,
            )
        )
        session.add(
            PanelDecisionModel(
                id=generate_id("panel"),
                proposal_id=proposal.id,
                section=section,
                symbol=proposal.symbol,
                confidence=proposal.panel_confidence,
                bull_summary=f"{signal.analyst} favours {proposal.side.value}.",
                bear_summary="Deterministic risk gate must still approve the trade.",
                synthesis_rationale=signal.features_ref,
            )
        )
        session.add(
            AgentRunModel(
                id=generate_id("run"),
                timestamp=stamp,
                agent_name="panel",
                model="deterministic-paper",
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=0.0,
                latency_ms=1,
            )
        )
        audit.record(
            session,
            event_type="proposal.created",
            actor="axel.agents.panel",
            message=(
                f"proposal {proposal.id} · {section} {proposal.symbol} "
                f"{proposal.side.value.upper()} entry={proposal.entry:.2f} "
                f"stop={proposal.stop:.2f} target={proposal.target:.2f} "
                f"conf={proposal.panel_confidence:.2f} · {proposal.strategy_id}"
            ),
            logger="axel.agents.panel",
            channel="CHAT",
            level="INFO",
            timestamp=stamp,
            proposal_id=proposal.id,
            section=section,
            symbol=proposal.symbol,
        )
        created += 1
    return created


def agent_once(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    stamp = now or datetime.now(UTC)
    signals = generate_signals(session, now=stamp)
    proposals = propose(session, now=stamp)
    return {"signals": signals, "proposals": proposals}


__all__ = ["ANALYSTS", "agent_once", "generate_signals", "propose"]
