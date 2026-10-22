"""
Event-driven triggers and analyst dispatcher for Phase 3 (Stocks Agent Layer).

Dispatches events (BarClose, FilingIngested, NewsIngested, MacroObservation)
to the appropriate specialized analyst agent.
"""

from __future__ import annotations

from collections.abc import Sequence

from axel.agents.analysts import (
    FundamentalAnalyst,
    MacroAnalyst,
    SentimentAnalyst,
    TechnicalAnalyst,
)
from axel.core.contracts import Signal
from axel.data.schemas import BarRecord, FilingRecord, MacroObservation, NewsRecord


class AgentEventDispatcher:
    """
    Coordinates incoming market data events and triggers schema-validated analyst runs.
    """

    def __init__(
        self,
        technical_analyst: TechnicalAnalyst | None = None,
        fundamental_analyst: FundamentalAnalyst | None = None,
        sentiment_analyst: SentimentAnalyst | None = None,
        macro_analyst: MacroAnalyst | None = None,
    ) -> None:
        self.technical = technical_analyst or TechnicalAnalyst()
        self.fundamental = fundamental_analyst or FundamentalAnalyst()
        self.sentiment = sentiment_analyst or SentimentAnalyst()
        self.macro = macro_analyst or MacroAnalyst()

    def on_bar_close(self, symbol: str, bars: Sequence[BarRecord]) -> Signal:
        """Triggered upon completion/closing of an OHLCV bar."""
        return self.technical.analyze_bars(symbol, bars)

    def on_filing_ingested(self, symbol: str, filing: FilingRecord) -> Signal:
        """Triggered when an SEC EDGAR filing is ingested."""
        return self.fundamental.analyze_filing(symbol, filing)

    def on_news_ingested(self, symbol: str, news: NewsRecord) -> Signal:
        """Triggered when a news item or article is ingested."""
        return self.sentiment.analyze_news(symbol, news)

    def on_macro_observation(self, symbol: str, observations: Sequence[MacroObservation]) -> Signal:
        """Triggered when a macro data vintage is recorded."""
        return self.macro.analyze_observations(symbol, observations)
