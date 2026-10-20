"""
Crypto Section Manager for Phase 7 (Crypto).
Aggregates crypto analyst signals, validates strategy membership and venue eligibility,
and emits strictly typed Proposal contracts to the deterministic risk core.
May abstain when conditions or edge are insufficient.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from axel.core.contracts import Proposal, Signal
from axel.core.types import Direction, OrderSide, Section, TradingMode
from axel.crypto.risk.venue_risk import VenueRiskManager

APPROVED_CRYPTO_STRATEGIES = frozenset(
    {
        "crypto:funding_arbitrage",
        "crypto:cross_exchange_arbitrage",
        "crypto:on_chain_flow",
    }
)


class CryptoSectionManager:
    """
    Manager for Section.CRYPTO.
    Enforces that proposals only map to approved strategies and eligible venues.
    """

    def __init__(
        self,
        venue_risk_manager: VenueRiskManager | None = None,
        min_consensus_strength: float = 0.30,
    ) -> None:
        self.venue_risk_manager = venue_risk_manager or VenueRiskManager()
        self.min_consensus_strength = min_consensus_strength

    def evaluate_proposal(
        self,
        strategy_id: str,
        symbol: str,
        venue: str,
        entry: float,
        stop: float,
        target: float,
        signals: Sequence[Signal],
        mode: TradingMode = TradingMode.PAPER,
    ) -> Proposal | None:
        """
        Build a typed Proposal if eligible, or return None to abstain.
        """
        # 1. Strategy membership validation
        if strategy_id not in APPROVED_CRYPTO_STRATEGIES:
            raise PermissionError(
                f"Strategy '{strategy_id}' is not an approved Crypto strategy. "
                f"Approved: {sorted(APPROVED_CRYPTO_STRATEGIES)}"
            )

        # 2. Venue eligibility check
        allowed, _reason = self.venue_risk_manager.is_venue_allowed(venue)
        if not allowed:
            # Abstain if venue is disabled/unhealthy
            return None

        # 3. Signals validation
        crypto_signals = [s for s in signals if s.section == Section.CRYPTO and s.symbol == symbol]
        if not crypto_signals:
            return None

        long_weight = sum(s.strength for s in crypto_signals if s.direction == Direction.LONG)
        short_weight = sum(s.strength for s in crypto_signals if s.direction == Direction.SHORT)
        total_weight = long_weight + short_weight

        if total_weight <= 0:
            return None

        net_strength = abs(long_weight - short_weight) / len(crypto_signals)
        if net_strength < self.min_consensus_strength:
            # Consensus too weak — abstain
            return None

        side = OrderSide.BUY if long_weight >= short_weight else OrderSide.SELL

        # Verify stop / target orientation before construction
        if side == OrderSide.BUY:
            if stop >= entry or target <= entry:
                return None  # Invalid geometry — abstain
        else:
            if stop <= entry or target >= entry:
                return None  # Invalid geometry — abstain

        confidence = min(max(net_strength, 0.0), 1.0)

        return Proposal(
            section=Section.CRYPTO,
            symbol=symbol,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            strategy_id=strategy_id,
            panel_confidence=round(confidence, 4),
            rationale_ref=f"crypto_manager:{venue}:{datetime.now(UTC).isoformat()}",
            mode=mode,
        )
