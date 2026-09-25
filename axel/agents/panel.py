"""Proposal-only aggregation of validated Signals."""

from datetime import UTC, datetime

from axel.core.contracts import Proposal, Signal
from axel.core.types import Direction, OrderSide, Section, TradingMode


def build_stock_proposal(signals: list[Signal], *, strategy_id: str, entry: float, stop: float, target: float) -> Proposal:
    if not signals or any(signal.section != Section.STOCKS for signal in signals):
        raise ValueError("Stocks panel requires Stocks signals")
    if not strategy_id.startswith("validated:"):
        raise PermissionError("panel may propose only validated strategies")
    long_weight = sum(signal.strength for signal in signals if signal.direction == Direction.LONG)
    short_weight = sum(signal.strength for signal in signals if signal.direction == Direction.SHORT)
    side = OrderSide.BUY if long_weight >= short_weight else OrderSide.SELL
    confidence = min(max(abs(long_weight - short_weight) / len(signals), 0.0), 1.0)
    return Proposal(section=Section.STOCKS, symbol=signals[0].symbol, side=side, entry=entry, stop=stop,
                    target=target, strategy_id=strategy_id, panel_confidence=confidence,
                    rationale_ref=f"panel:{datetime.now(UTC).isoformat()}", mode=TradingMode.PAPER)
