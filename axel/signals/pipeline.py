"""
Signal ingestion pipeline: Telegram text -> Signal record -> (risk-gated Proposal).

Trust boundary: Telegram text is DATA. The pipeline classifies/parses deterministically,
persists a Signal, and only for complete trade signals builds a Proposal that is passed
to the SectionRiskAgent (the same deterministic gate as every other proposal). Betting
signals are recorded and reconciled against the daily AllocationBankroll cap instead of
the broker path.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from axel.core.contracts import Proposal, Signal
from axel.core.types import Direction, OrderSide, Section, TradingMode
from axel.db.models import SignalModel, TradeProposalModel
from axel.risk.engine import AccountState, SectionRiskAgent, SectionRiskState
from axel.risk.limits import LIMITS
from axel.signals.bankroll import AllocationBankroll
from axel.signals.classify import SignalKind, classify_message
from axel.signals.parsers import BetSignal, TradeSignal, parse_bet_signal, parse_trading_signal
from axel.signals.source import TelegramMessage

_DEFAULT_EQUITY = 100_000.0
_FOREX_NOT_WIRED = "forex section is not yet wired to the risk engine; signal recorded"


@dataclass
class IngestedSignal:
    """Result of ingesting one Telegram message through the signal pipeline."""

    kind: SignalKind
    reason: str
    parsed: BetSignal | TradeSignal | None = None
    signal_record: SignalModel | None = None
    proposal: Proposal | None = None
    decision: object | None = None
    persisted: bool = False


def _account_defaults() -> AccountState:
    return AccountState(
        total_equity=_DEFAULT_EQUITY,
        cash=_DEFAULT_EQUITY,
        global_drawdown_pct=0.0,
        kill_switch_active=False,
    )


def _section_defaults(section: Section) -> SectionRiskState:
    return SectionRiskState(
        section=section,
        section_nav=_DEFAULT_EQUITY,
        start_of_day_equity=_DEFAULT_EQUITY,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        active_positions_count=0,
    )


def _build_proposal(parsed: TradeSignal, chat: str) -> Proposal:
    section = Section.CRYPTO if parsed.market == "crypto" else Section.STOCKS
    side = parsed.side or OrderSide.BUY
    return Proposal(
        section=section,
        symbol=parsed.symbol,
        side=side,
        entry=parsed.entry,
        stop=parsed.stop,
        target=parsed.target,
        strategy_id=f"telegram:{chat}",
        panel_confidence=parsed.confidence,
        win_rate=0.5,
        mode=TradingMode.PAPER,
    )


def _levels_are_valid(parsed: TradeSignal) -> bool:
    if parsed.entry is None or parsed.stop is None or parsed.target is None:
        return False
    if parsed.side is None:
        return False
    if parsed.side == OrderSide.BUY:
        return parsed.entry > parsed.stop and parsed.target > parsed.entry
    return parsed.entry < parsed.stop and parsed.target < parsed.entry


def _record_signal(session: Session, signal: Signal) -> SignalModel:
    row = SignalModel(
        id=signal.id,
        ts=signal.ts,
        as_of=signal.as_of,
        section=signal.section.value,
        symbol=signal.symbol,
        analyst=signal.analyst,
        direction=signal.direction.value,
        strength=signal.strength,
        horizon=signal.horizon,
        features_ref=signal.features_ref,
    )
    session.add(row)
    session.flush()
    return row


def _record_proposal(session: Session, proposal: Proposal) -> TradeProposalModel:
    from datetime import UTC, datetime, timedelta

    row = TradeProposalModel(
        id=proposal.id,
        section=proposal.section.value,
        symbol=proposal.symbol,
        side=proposal.side.value,
        entry_price=proposal.entry,
        stop_loss=proposal.stop,
        take_profit=proposal.target,
        confidence_score=proposal.panel_confidence,
        strategy_id=proposal.strategy_id,
        mode=proposal.mode.value,
        status="pending",
        ttl_expires_at=datetime.now(UTC) + timedelta(hours=4),
        agent_reasoning=f"telegram:{proposal.symbol} signal",
    )
    session.add(row)
    session.flush()
    return row


class TeleSignalPipeline:
    """Runs one Telegram message through classify -> parse -> record -> risk gate."""

    def __init__(
        self,
        session: Session | None = None,
        bankroll: AllocationBankroll | None = None,
        min_trade_confidence: float = 0.50,
        risk_agent: SectionRiskAgent | None = None,
    ) -> None:
        from axel.db.session import SessionLocal

        self._session_factory = (lambda: session) if session is not None else SessionLocal
        self.bankroll = bankroll or AllocationBankroll()
        self.bankroll.initialize(self.bankroll.balance or _DEFAULT_EQUITY)
        self.min_trade_confidence = min_trade_confidence
        self.risk_agent = risk_agent or SectionRiskAgent(LIMITS)

    def _session(self) -> Session:
        return self._session_factory()

    def ingest(self, message: TelegramMessage) -> IngestedSignal:
        return self.ingest_text(message.text, chat=message.chat)

    def ingest_text(self, text: str, chat: str = "unknown") -> IngestedSignal:
        kind = classify_message(text, source_hint=chat)
        if kind == SignalKind.NOISE:
            return IngestedSignal(kind=kind, reason="no signal content", persisted=False)

        session = self._session()
        try:
            if kind == SignalKind.BET_SIGNAL:
                return self._ingest_bet(session, text, chat, kind)
            return self._ingest_trade(session, text, chat, kind)
        finally:
            session.close()

    def _ingest_bet(self, session: Session, text: str, chat: str, kind: SignalKind) -> IngestedSignal:
        parsed = parse_bet_signal(text, source=chat)
        if parsed is None:
            return IngestedSignal(kind=kind, reason="bet keywords present but no valid booking code")
        signal = Signal(
            section=Section.PREDICTIONS,
            symbol=f"BET:{parsed.code}",
            analyst=f"telegram:{chat}",
            direction=Direction.LONG,
            strength=parsed.confidence,
        )
        record = _record_signal(session, signal)
        session.commit()
        remaining = self.bankroll.remaining()
        return IngestedSignal(
            kind=kind,
            reason=(
                f"bet signal recorded; code {parsed.code} confidence {parsed.confidence:.2f}; "
                f"staking subject to daily allocation cap (remaining {remaining:.2f})"
            ),
            parsed=parsed,
            signal_record=record,
            persisted=True,
        )

    def _ingest_trade(self, session: Session, text: str, chat: str, kind: SignalKind) -> IngestedSignal:
        parsed = parse_trading_signal(text, source=chat)
        if parsed is None:
            return IngestedSignal(kind=kind, reason="trading keywords present but no parseable symbol/levels")

        section = Section.CRYPTO if parsed.market == "crypto" else Section.STOCKS
        signal = Signal(
            section=section,
            symbol=parsed.symbol,
            analyst=f"telegram:{chat}",
            direction=Direction.LONG if parsed.side == OrderSide.BUY else Direction.SHORT,
            strength=parsed.confidence,
        )
        record = _record_signal(session, signal)

        if parsed.market == "forex":
            session.commit()
            return IngestedSignal(
                kind=kind,
                reason=_FOREX_NOT_WIRED,
                parsed=parsed,
                signal_record=record,
                persisted=True,
            )

        if not _levels_are_valid(parsed):
            session.commit()
            return IngestedSignal(
                kind=kind,
                reason="incomplete or invalid entry/stop/target levels; signal recorded for review",
                parsed=parsed,
                signal_record=record,
                persisted=True,
            )

        if parsed.confidence < self.min_trade_confidence:
            session.commit()
            return IngestedSignal(
                kind=kind,
                reason=f"below min trade confidence {self.min_trade_confidence:.2f}; signal recorded",
                parsed=parsed,
                signal_record=record,
                persisted=True,
            )

        proposal = _build_proposal(parsed, chat)
        _record_proposal(session, proposal)

        decision = self.risk_agent.evaluate(
            proposal=proposal,
            account_state=_account_defaults(),
            section_state=_section_defaults(proposal.section),
        )
        session.commit()

        verdict = decision.verdict.value if hasattr(decision.verdict, "value") else str(decision.verdict)
        outcome = "approved" if decision.approved else "rejected"
        binding = decision.binding_limit or "n/a"
        return IngestedSignal(
            kind=kind,
            reason=f"{outcome} by risk engine ({verdict}; binding limit: {binding})",
            parsed=parsed,
            signal_record=record,
            proposal=proposal,
            decision=decision,
            persisted=True,
        )