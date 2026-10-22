"""
Market Microstructure Analyst for Phase 7 (Crypto).
Analyzes order book imbalance, spread widening, and queue depth skew.
Emits typed Signal objects across the trust boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from axel.core.contracts import Signal
from axel.core.types import Direction, Section
from axel.crypto.schemas import CryptoOrderBookRecord


class MarketMicrostructureAnalyst:
    """
    Evaluates short-term order book liquidity and depth imbalance.
    Heavy bid depth relative to ask depth indicates short-term support -> LONG.
    Heavy ask depth relative to bid depth indicates overhead resistance -> SHORT.
    """

    name = "crypto_microstructure_analyst"

    def analyze(
        self,
        book: CryptoOrderBookRecord,
        levels: int = 5,
        as_of: datetime | None = None,
    ) -> Signal:
        as_of = as_of or datetime.now(UTC)

        bid_depth = book.bid_depth_usd(levels)
        ask_depth = book.ask_depth_usd(levels)
        total_depth = bid_depth + ask_depth

        if total_depth <= Decimal("0.0"):
            return Signal(
                section=Section.CRYPTO,
                symbol=book.symbol,
                analyst=self.name,
                direction=Direction.LONG,
                strength=0.0,
                horizon="1h",
                as_of=as_of,
                features_ref="zero_depth",
            )

        imbalance = (bid_depth - ask_depth) / total_depth
        strength = float(abs(imbalance))

        direction = Direction.LONG if imbalance >= Decimal("0.0") else Direction.SHORT

        return Signal(
            section=Section.CRYPTO,
            symbol=book.symbol,
            analyst=self.name,
            direction=direction,
            strength=round(min(strength, 1.0), 4),
            horizon="1h",
            as_of=as_of,
            features_ref=f"spread_bps={book.spread_bps:.2f},bid_d=${bid_depth:,.0f},ask_d=${ask_depth:,.0f}",
        )
