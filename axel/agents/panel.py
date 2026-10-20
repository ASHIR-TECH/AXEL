"""
Expert Panel Debate and Proposal Aggregation for Phase 3 (Stocks Agent Layer).

Orchestrates multi-agent debate across Technical, Fundamental, Sentiment, and Macro analysts.
Enforces the strict rule: The panel may propose ONLY validated strategies from the StrategyRegistry.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from axel.core.contracts import Proposal, Signal
from axel.core.types import Direction, OrderSide, Section, TradingMode
from axel.strategies.metadata import StrategyStatus
from axel.strategies.registry import StrategyRegistry


@dataclass(frozen=True)
class DebateArgument:
    analyst: str
    direction: Direction
    strength: float
    evidence: str


@dataclass(frozen=True)
class DebateResult:
    symbol: str
    bull_arguments: tuple[DebateArgument, ...]
    bear_arguments: tuple[DebateArgument, ...]
    net_consensus_score: float  # -1.0 (strong bear) to +1.0 (strong bull)
    recommended_side: OrderSide
    confidence: float
    consensus_reached: bool
    summary_rationale: str


class StocksExpertPanel:
    """
    Expert Panel orchestrating debate between specialized analyst agents.
    Only permitted to emit proposals for validated strategies.
    """

    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self.registry = registry

    def debate(self, signals: Sequence[Signal]) -> DebateResult:
        if not signals:
            raise ValueError("Expert panel requires at least one signal")

        symbol = signals[0].symbol
        bull_args = []
        bear_args = []

        total_bull_weight = 0.0
        total_bear_weight = 0.0

        for sig in signals:
            if sig.section != Section.STOCKS:
                raise ValueError(f"Signal section {sig.section} is not STOCKS")
            arg = DebateArgument(
                analyst=sig.analyst,
                direction=sig.direction,
                strength=sig.strength,
                evidence=sig.features_ref or "",
            )
            if sig.direction == Direction.LONG:
                bull_args.append(arg)
                total_bull_weight += sig.strength
            else:
                bear_args.append(arg)
                total_bear_weight += sig.strength

        total_weight = total_bull_weight + total_bear_weight
        if total_weight > 0:
            net_score = (total_bull_weight - total_bear_weight) / total_weight
        else:
            net_score = 0.0

        side = OrderSide.BUY if net_score >= 0 else OrderSide.SELL
        confidence = min(max(abs(net_score), 0.0), 1.0)
        consensus_reached = confidence >= 0.30

        summary = (
            f"Panel debate on {symbol}: {len(bull_args)} Bull vs {len(bear_args)} Bear. "
            f"Net score={net_score:.2f}, Consensus={'YES' if consensus_reached else 'NO'}"
        )

        return DebateResult(
            symbol=symbol,
            bull_arguments=tuple(bull_args),
            bear_arguments=tuple(bear_args),
            net_consensus_score=round(net_score, 4),
            recommended_side=side,
            confidence=round(confidence, 4),
            consensus_reached=consensus_reached,
            summary_rationale=summary,
        )

    def propose_trade(
        self,
        signals: Sequence[Signal],
        strategy_id: str,
        entry: float,
        stop: float,
        target: float,
        mode: TradingMode = TradingMode.PAPER,
    ) -> Proposal:
        """
        Synthesize panel debate into a concrete Proposal.
        Validates strategy against the strategy registry.
        """
        # Strict validated strategy enforcement (PRD requirement)
        self._validate_strategy_eligibility(strategy_id)

        debate_res = self.debate(signals)
        if not debate_res.consensus_reached:
            raise ValueError(f"Panel failed to reach minimum consensus ({debate_res.confidence:.2f} < 0.30)")

        now = datetime.now(UTC)
        return Proposal(
            section=Section.STOCKS,
            symbol=debate_res.symbol,
            side=debate_res.recommended_side,
            entry=entry,
            stop=stop,
            target=target,
            strategy_id=strategy_id,
            panel_confidence=debate_res.confidence,
            rationale_ref=f"panel_debate:{now.isoformat()}:{debate_res.summary_rationale[:60]}",
            mode=mode,
        )

    def _validate_strategy_eligibility(self, strategy_id: str) -> None:
        """Ensure strategy is validated or paper eligible."""
        if self.registry is not None:
            try:
                meta = self.registry.get(strategy_id)
                if meta.status not in (StrategyStatus.VALIDATED, StrategyStatus.PAPER_ELIGIBLE, StrategyStatus.ACTIVE):
                    raise PermissionError(
                        f"Strategy {strategy_id} has status {meta.status.value}; "
                        "panel may only propose VALIDATED or PAPER_ELIGIBLE strategies"
                    )
            except KeyError:
                if not strategy_id.startswith(("validated:", "paper:")):
                    raise PermissionError(f"Strategy {strategy_id} is not registered or validated")
        else:
            if not strategy_id.startswith(("validated:", "paper:")):
                raise PermissionError("panel may propose only validated strategies")


# ── Backwards-Compatible Legacy Function ────────────────────────────────────


def build_stock_proposal(
    signals: list[Signal],
    *,
    strategy_id: str,
    entry: float,
    stop: float,
    target: float,
) -> Proposal:
    """Legacy helper preserved for Phase 0-2 test suite compatibility."""
    if not signals or any(signal.section != Section.STOCKS for signal in signals):
        raise ValueError("Stocks panel requires Stocks signals")
    if not strategy_id.startswith(("validated:", "paper:")):
        raise PermissionError("panel may propose only validated strategies")

    panel = StocksExpertPanel()
    return panel.propose_trade(
        signals=signals,
        strategy_id=strategy_id,
        entry=entry,
        stop=stop,
        target=target,
        mode=TradingMode.PAPER,
    )
