"""
Regime & Risk Analyst for Phase 7 (Crypto).
Classifies crypto volatility and liquidity regimes.
Emits typed Signal objects across the trust boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime

from axel.core.contracts import Signal
from axel.core.types import Direction, Section
from axel.crypto.types import CryptoMarketRegime


class RegimeRiskAnalyst:
    """
    Evaluates crypto market volatility regimes:
    - Low-vol range: optimal for funding carry and mean reversion.
    - High-vol trending: favorable for momentum, higher risk for arbitrage.
    - Extreme dislocation: danger zone, capital preservation / abstain.
    """

    name = "crypto_regime_analyst"

    def classify_regime(
        self,
        realized_vol: float,
        spread_bps: float,
    ) -> CryptoMarketRegime:
        if realized_vol > 1.20 or spread_bps > 50.0:  # >120% annualized vol or huge spread
            return CryptoMarketRegime.EXTREME_VOL_DISLOCATION
        elif realized_vol > 0.65:
            return CryptoMarketRegime.HIGH_VOL_TRENDING
        elif spread_bps > 20.0:
            return CryptoMarketRegime.COMPRESSED_LIQUIDITY
        else:
            return CryptoMarketRegime.LOW_VOL_RANGE

    def analyze(
        self,
        symbol: str,
        realized_vol: float,
        spread_bps: float,
        as_of: datetime | None = None,
    ) -> Signal:
        as_of = as_of or datetime.now(UTC)
        regime = self.classify_regime(realized_vol, spread_bps)

        # In extreme dislocation, strength drops to 0 (signals caution)
        if regime == CryptoMarketRegime.EXTREME_VOL_DISLOCATION:
            strength = 0.0
            direction = Direction.SHORT
        elif regime == CryptoMarketRegime.LOW_VOL_RANGE:
            strength = 0.8
            direction = Direction.LONG
        else:
            strength = 0.5
            direction = Direction.LONG

        return Signal(
            section=Section.CRYPTO,
            symbol=symbol,
            analyst=self.name,
            direction=direction,
            strength=strength,
            horizon="24h",
            as_of=as_of,
            features_ref=f"regime={regime.value},vol={realized_vol:.2f},spread={spread_bps:.1f}bps",
        )
