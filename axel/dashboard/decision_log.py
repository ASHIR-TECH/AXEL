"""Read-only decision-log projection for the Phase 4 dashboard."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from axel.db.models import RiskDecisionModel, TradeProposalModel


def recent_decisions(session: Session, limit: int = 100) -> list[dict[str, object]]:
    """Return traceable proposal-to-risk records; deliberately has no mutation API."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    query = (select(TradeProposalModel, RiskDecisionModel)
             .outerjoin(RiskDecisionModel, RiskDecisionModel.proposal_id == TradeProposalModel.id)
             .order_by(TradeProposalModel.created_at.desc()).limit(limit))
    return [{"proposal_id": proposal.id, "symbol": proposal.symbol, "strategy_id": proposal.strategy_id,
             "confidence": proposal.confidence_score, "status": proposal.status,
             "risk_verdict": decision.verdict if decision else None,
             "risk_reasons": decision.reasons if decision else []}
            for proposal, decision in session.execute(query)]
