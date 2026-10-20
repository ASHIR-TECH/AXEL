"""
Funding Analyst for Phase 7 (Crypto).
Analyzes perpetual funding rates, rate differentials, basis, and predicted funding.
Emits typed Signal objects across the trust boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from axel.core.contracts import Signal
from axel.core.types import Direction, Section
from axel.crypto.schemas import FundingRateRecord


class FundingAnalyst:
    """
    Evaluates funding rate carry and mean-reversion signals.
    When funding is heavily positive, longs pay shorts (bearish for spot/perp basis, favorable for short perp carry).
    When funding is heavily negative, shorts pay longs (bullish for spot/perp basis, favorable for long perp carry).
    """

    name = "crypto_funding_analyst"

    def __init__(self, high_funding_threshold: Decimal = Decimal("0.0003")) -> None:  # 0.03% per 8h (≈33% APR)
        self.high_funding_threshold = high_funding_threshold

    def analyze(
        self,
        record: FundingRateRecord,
        as_of: datetime | None = None,
    ) -> Signal:
        as_of = as_of or datetime.now(UTC)

        rate = record.rate
        abs_rate = abs(rate)

        # Strength proportional to funding extremity (capped at 1.0)
        strength = min(float(abs_rate / self.high_funding_threshold), 1.0)

        # High positive funding: Perp is premium, expect mean-reversion downwards or short carry edge
        # High negative funding: Perp is discount, expect mean-reversion upwards or long carry edge
        if rate >= Decimal("0.0"):
            direction = Direction.SHORT  # Short perp / long spot carry
        else:
            direction = Direction.LONG   # Long perp / short spot carry

        return Signal(
            section=Section.CRYPTO,
            symbol=record.symbol,
            analyst=self.name,
            direction=direction,
            strength=round(strength, 4),
            horizon="8h",
            as_of=as_of,
            features_ref=f"rate={record.rate:.6f},pred={record.predicted_rate or 0:.6f}",
        )
