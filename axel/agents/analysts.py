"""
Schema-first Stocks analysts for Phase 3.
Covers Technical, Fundamental (SEC EDGAR), Sentiment (News), and Macro (FRED) analysis.
Input text is data, never an instruction.
All analysts emit strictly schema-validated Signal contracts across the trust boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from axel.agents.untrusted import PromptSanitizer, UntrustedDocument
from axel.core.contracts import Signal
from axel.core.types import Direction, Section
from axel.data.schemas import BarRecord, FilingRecord, MacroObservation, NewsRecord


class TechnicalAnalyst:
    """
    Computes deterministic technical momentum, moving average trends, and RSI.
    """

    name = "technical"

    def analyze_bars(
        self,
        symbol: str,
        bars: Sequence[BarRecord],
        as_of: datetime | None = None,
    ) -> Signal:
        if not bars:
            raise ValueError("Bars sequence cannot be empty")

        as_of = as_of or datetime.now(UTC)
        closes = [b.close for b in bars]
        current_price = closes[-1]

        if current_price <= 0:
            raise ValueError("Current price must be positive")

        # Moving average
        window = min(len(closes), 20)
        ma = sum(closes[-window:]) / window

        # Simple momentum: price vs MA
        ma_diff_pct = (current_price / ma) - 1.0

        # RSI over 14 periods if available
        rsi = 50.0
        if len(closes) >= 15:
            gains, losses = [], []
            for i in range(len(closes) - 14, len(closes)):
                diff = closes[i] - closes[i - 1]
                if diff > 0:
                    gains.append(diff)
                else:
                    losses.append(abs(diff))
            avg_gain = sum(gains) / 14 if gains else 0.0001
            avg_loss = sum(losses) / 14 if losses else 0.0001
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        # Direction logic:
        # Bullish if above MA and RSI not overbought (>70)
        # Bearish if below MA and RSI not oversold (<30)
        if current_price >= ma:
            direction = Direction.LONG
            strength = min(abs(ma_diff_pct) * 10.0, 1.0)
            if rsi > 70:
                strength *= 0.5  # Mean reversion haircut on overbought
        else:
            direction = Direction.SHORT
            strength = min(abs(ma_diff_pct) * 10.0, 1.0)
            if rsi < 30:
                strength *= 0.5  # Mean reversion haircut on oversold

        return Signal(
            section=Section.STOCKS,
            symbol=symbol.upper(),
            analyst=self.name,
            direction=direction,
            strength=round(max(strength, 0.0), 4),
            horizon="1d",
            as_of=as_of,
            features_ref=f"close={current_price:.2f},ma20={ma:.2f},rsi={rsi:.1f}",
        )


class FundamentalAnalyst:
    """
    Evaluates SEC EDGAR filings (10-K, 10-Q) for revenue growth and balance sheet strength.
    """

    name = "fundamental"

    def analyze_filing(
        self,
        symbol: str,
        filing: FilingRecord,
        as_of: datetime | None = None,
    ) -> Signal:
        as_of = as_of or datetime.now(UTC)
        fields_dict = dict(filing.fields)

        # Parse fundamental indicators if present
        revenue_growth_str = fields_dict.get("revenue_growth_yoy", "0.0")
        debt_to_equity_str = fields_dict.get("debt_to_equity", "1.0")

        try:
            rev_growth = float(revenue_growth_str)
            de_ratio = float(debt_to_equity_str)
        except ValueError:
            rev_growth = 0.0
            de_ratio = 1.0

        # Bullish: strong revenue growth (>5%) and manageable debt (<2.0)
        # Bearish: declining revenue (<0%) or high leverage (>3.0)
        if rev_growth > 0.05 and de_ratio < 2.0:
            direction = Direction.LONG
            strength = min(rev_growth * 5.0, 1.0)
        elif rev_growth < -0.05 or de_ratio > 3.0:
            direction = Direction.SHORT
            strength = min(abs(rev_growth) * 5.0, 1.0)
        else:
            direction = Direction.LONG
            strength = 0.30

        return Signal(
            section=Section.STOCKS,
            symbol=symbol.upper(),
            analyst=self.name,
            direction=direction,
            strength=round(max(strength, 0.0), 4),
            horizon="30d",
            as_of=as_of,
            features_ref=f"form={filing.form},rev_growth={rev_growth:.1%},de={de_ratio:.2f}",
        )


class SentimentAnalyst:
    """
    Evaluates news headlines and content with prompt-injection defense.
    Sandboxes untrusted external text.
    """

    name = "sentiment"

    # Pre-compiled sentiment lexicon for deterministic analysis
    BULLISH_KEYWORDS = frozenset({"surge", "beat", "record", "growth", "upgrade", "profit", "bullish", "jump", "soar", "gain"})
    BEARISH_KEYWORDS = frozenset({"drop", "miss", "plunge", "decline", "downgrade", "loss", "bearish", "crash", "investigation", "probe"})

    def analyze_news(
        self,
        symbol: str,
        news: NewsRecord,
        as_of: datetime | None = None,
    ) -> Signal:
        as_of = as_of or datetime.now(UTC)

        # 1. Sanitize untrusted document
        doc = UntrustedDocument(source=news.publisher, text=f"{news.title} {news.body_ref}", published_at=news.event_time)
        sanitized = PromptSanitizer.sanitize(doc)

        # 2. Text token analysis
        content_lower = f"{news.title} {news.body_ref}".lower()
        words = content_lower.split()

        bull_count = sum(1 for w in words if w in self.BULLISH_KEYWORDS)
        bear_count = sum(1 for w in words if w in self.BEARISH_KEYWORDS)

        total = bull_count + bear_count
        if total == 0:
            direction = Direction.LONG
            strength = 0.10
        elif bull_count >= bear_count:
            direction = Direction.LONG
            strength = min((bull_count - bear_count + 1) / (total + 1), 1.0)
        else:
            direction = Direction.SHORT
            strength = min((bear_count - bull_count + 1) / (total + 1), 1.0)

        # If injection attempt was detected, haircut strength by 50%
        if sanitized["is_flagged"] == "true":
            strength *= 0.50

        return Signal(
            section=Section.STOCKS,
            symbol=symbol.upper(),
            analyst=self.name,
            direction=direction,
            strength=round(max(strength, 0.0), 4),
            horizon="1d",
            as_of=as_of,
            features_ref=f"publisher={news.publisher},bull={bull_count},bear={bear_count},flagged={sanitized['is_flagged']}",
        )


class MacroAnalyst:
    """
    Evaluates FRED macroeconomic indicators (e.g. 10Y yield DGS10, CPI inflation, unemployment).
    """

    name = "macro"

    def analyze_observations(
        self,
        symbol: str,
        observations: Sequence[MacroObservation],
        as_of: datetime | None = None,
    ) -> Signal:
        as_of = as_of or datetime.now(UTC)

        if not observations:
            return Signal(
                section=Section.STOCKS,
                symbol=symbol.upper(),
                analyst=self.name,
                direction=Direction.LONG,
                strength=0.20,
                horizon="7d",
                as_of=as_of,
                features_ref="no_macro_data",
            )

        latest = observations[-1]
        val = latest.value

        # Example series interpretation: DGS10 (10Y Treasury yield)
        # Yields rising above 4.5% -> tightening conditions -> headwind (SHORT bias or reduced risk)
        # Yields steady 2.5% - 3.5% -> supportive macro -> LONG bias
        if "DGS10" in latest.series_id:
            if val > 4.5:
                direction = Direction.SHORT
                strength = min((val - 4.5) / 2.0, 1.0)
            else:
                direction = Direction.LONG
                strength = 0.60
        else:
            direction = Direction.LONG
            strength = 0.50

        return Signal(
            section=Section.STOCKS,
            symbol=symbol.upper(),
            analyst=self.name,
            direction=direction,
            strength=round(max(strength, 0.0), 4),
            horizon="7d",
            as_of=as_of,
            features_ref=f"series={latest.series_id},val={val:.2f},vintage={latest.vintage}",
        )


# ── Backwards-Compatibility Layer ───────────────────────────────────────────


class StocksAnalyst:
    """Legacy baseline technical analyst retained for Phase 0-2 backwards compatibility."""

    name = "technical"

    def signal(self, symbol: str, current_price: float, moving_average: float, as_of: datetime | None = None) -> Signal:
        if current_price <= 0 or moving_average <= 0:
            raise ValueError("prices must be positive")
        direction = Direction.LONG if current_price >= moving_average else Direction.SHORT
        strength = min(abs(current_price / moving_average - 1) * 10, 1.0)
        return Signal(
            section=Section.STOCKS,
            symbol=symbol.upper(),
            analyst=self.name,
            direction=direction,
            strength=strength,
            as_of=as_of or datetime.now(UTC),
        )


def sanitize_document(document: UntrustedDocument) -> dict[str, str]:
    """Legacy helper preserved for backward compatibility."""
    return PromptSanitizer.sanitize(document)
