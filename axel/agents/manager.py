"""
Stocks Section Manager for Phase 3 (Stocks Agent Layer).
Evaluates panel proposals against section constraints, risk geometry, and universe eligibility.
"""

from __future__ import annotations

from axel.core.contracts import Proposal
from axel.core.types import OrderSide, Section, TradingMode
from axel.strategies.registry import StrategyRegistry


class StocksSectionManager:
    """
    Manager for Section.STOCKS.
    Enforces section boundaries, candidate filtering, and proposal validation.
    """

    def __init__(
        self,
        registry: StrategyRegistry | None = None,
        allowed_universe: frozenset[str] | None = None,
        min_reward_risk_ratio: float = 1.5,
    ) -> None:
        self.registry = registry
        self.allowed_universe = allowed_universe or frozenset({"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "SPY", "QQQ"})
        self.min_reward_risk_ratio = min_reward_risk_ratio

    def evaluate_proposal(
        self,
        symbol: str,
        side: OrderSide,
        entry: float,
        stop: float,
        target: float,
        strategy_id: str,
        panel_confidence: float,
        rationale_ref: str | None = None,
        mode: TradingMode = TradingMode.PAPER,
    ) -> Proposal | None:
        """
        Validates trade geometry and universe before submitting to deterministic core.
        Abstains (returns None) if any criteria are unmet.
        """
        symbol_upper = symbol.upper()

        # 1. Universe eligibility check
        if self.allowed_universe and symbol_upper not in self.allowed_universe:
            return None

        # 2. Risk/Reward ratio check
        if side == OrderSide.BUY:
            risk = entry - stop
            reward = target - entry
        else:
            risk = stop - entry
            reward = entry - target

        if risk <= 0 or reward <= 0:
            return None

        rr_ratio = reward / risk
        if rr_ratio < self.min_reward_risk_ratio:
            return None

        # 3. Minimum panel confidence check
        if panel_confidence < 0.30:
            return None

        # 4. Strategy validation
        if not strategy_id.startswith(("validated:", "paper:")):
            if self.registry is not None:
                try:
                    meta = self.registry.get(strategy_id)
                    if meta.status.value not in ("validated", "paper_eligible", "active"):
                        return None
                except KeyError:
                    return None
            else:
                return None

        return Proposal(
            section=Section.STOCKS,
            symbol=symbol_upper,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            strategy_id=strategy_id,
            panel_confidence=panel_confidence,
            rationale_ref=rationale_ref,
            mode=mode,
        )
