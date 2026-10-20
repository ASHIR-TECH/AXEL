"""
On-Chain Flow Strategy for Phase 7 (Crypto).

Generates contextual trade proposals based on net exchange outflows (accumulation)
and verified entity wallet balance shifts.
Rule: On-chain evidence is contextual and does not automatically imply a trade without confirmation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from axel.core.contracts import Proposal
from axel.core.types import OrderSide, Section, TradingMode
from axel.crypto.schemas import OnChainObservation
from axel.crypto.types import OnChainMetricType


@dataclass(frozen=True)
class OnChainStrategyParameters:
    min_confidence: float = 0.60
    min_net_outflow_usd: Decimal = Decimal("5000000.0")  # $5M net outflow
    take_profit_pct: Decimal = Decimal("0.04")            # 4%
    stop_loss_pct: Decimal = Decimal("0.02")              # 2%


class OnChainFlowStrategy:
    """
    Evaluates verified on-chain accumulation patterns.
    """

    strategy_id = "crypto:on_chain_flow"

    def __init__(self, params: OnChainStrategyParameters | None = None) -> None:
        self.params = params or OnChainStrategyParameters()

    def evaluate(
        self,
        symbol: str,
        current_price: Decimal,
        observations: Sequence[OnChainObservation],
        as_of: datetime | None = None,
    ) -> Proposal | None:
        if not observations or current_price <= Decimal(0):
            return None

        as_of = as_of or datetime.now(UTC)

        inflow_usd = Decimal(0)
        outflow_usd = Decimal(0)
        has_unverified = False

        for obs in observations:
            if not obs.is_verified_label:
                has_unverified = True
            if obs.metric_type == OnChainMetricType.EXCHANGE_INFLOW:
                inflow_usd += obs.metric_value
            elif obs.metric_type == OnChainMetricType.EXCHANGE_OUTFLOW:
                outflow_usd += obs.metric_value

        net_outflow = outflow_usd - inflow_usd

        # If unverified, haircut confidence
        confidence = 0.75 if not has_unverified else 0.40
        if confidence < self.params.min_confidence:
            return None

        # Bullish accumulation: significant net outflow from exchanges to cold storage
        if net_outflow >= self.params.min_net_outflow_usd:
            entry = float(current_price)
            stop = float(current_price * (Decimal("1.0") - self.params.stop_loss_pct))
            target = float(current_price * (Decimal("1.0") + self.params.take_profit_pct))

            latest_block = observations[-1].block_number
            provider = observations[-1].provider

            return Proposal(
                section=Section.CRYPTO,
                symbol=symbol,
                side=OrderSide.BUY,
                entry=entry,
                stop=stop,
                target=target,
                strategy_id=self.strategy_id,
                panel_confidence=confidence,
                rationale_ref=f"onchain_flow:{provider}:blk_{latest_block}:outflow=${net_outflow:,.0f}",
                mode=TradingMode.PAPER,
            )

        return None
