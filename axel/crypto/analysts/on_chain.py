"""
On-Chain Analyst for Phase 7 (Crypto).
Processes on-chain exchange flows and accumulation metrics.
Rule: Unverified wallet labels are never treated as facts.
Emits typed Signal objects across the trust boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from axel.core.contracts import Signal
from axel.core.types import Direction, Section
from axel.crypto.schemas import OnChainObservation
from axel.crypto.types import OnChainMetricType


class OnChainAnalyst:
    """
    Evaluates on-chain accumulation and exchange net flow.
    Large exchange net inflow -> increased selling pressure -> SHORT bias.
    Large exchange net outflow -> coins moved to cold storage -> LONG bias.
    Unverified entity labels have confidence halved.
    """

    name = "crypto_onchain_analyst"

    def analyze(
        self,
        symbol: str,
        observations: Sequence[OnChainObservation],
        as_of: datetime | None = None,
    ) -> Signal:
        as_of = as_of or datetime.now(UTC)

        if not observations:
            return Signal(
                section=Section.CRYPTO,
                symbol=symbol,
                analyst=self.name,
                direction=Direction.LONG,
                strength=0.0,
                horizon="24h",
                as_of=as_of,
                features_ref="no_onchain_data",
            )

        total_inflow = Decimal("0.0")
        total_outflow = Decimal("0.0")
        has_unverified = False

        for obs in observations:
            if not obs.is_verified_label:
                has_unverified = True
            if obs.metric_type == OnChainMetricType.EXCHANGE_INFLOW:
                total_inflow += obs.metric_value
            elif obs.metric_type == OnChainMetricType.EXCHANGE_OUTFLOW:
                total_outflow += obs.metric_value

        net_flow = total_inflow - total_outflow
        total_volume = total_inflow + total_outflow

        if total_volume > Decimal("0.0"):
            flow_imbalance = abs(net_flow) / total_volume
            strength = float(min(flow_imbalance, Decimal("1.0")))
        else:
            strength = 0.0

        # Unverified wallet labels degrade confidence/strength (PRD requirement)
        if has_unverified:
            strength *= 0.5

        # Net inflow -> potential selling -> SHORT
        # Net outflow -> accumulation -> LONG
        direction = Direction.SHORT if net_flow > Decimal("0.0") else Direction.LONG

        latest_block = observations[-1].block_number
        provider = observations[-1].provider

        return Signal(
            section=Section.CRYPTO,
            symbol=symbol,
            analyst=self.name,
            direction=direction,
            strength=round(strength, 4),
            horizon="24h",
            as_of=as_of,
            features_ref=f"provider={provider},block={latest_block},net_flow={net_flow},unverified={has_unverified}",
        )
