"""
Cross-Exchange Arbitrage Strategy for Phase 7 (Crypto).

Detects synchronized price discrepancies between two venues (Buy Venue A, Sell Venue B).
Models both legs, taker fees, depth slippage, execution latency, and settlement.
Strict Rule: Explicit repair / hedge / incident path for one-leg execution failure.
Simultaneous perfect fills are never assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from axel.crypto.schemas import ArbitrageSpreadOpportunity, CryptoOrderBookRecord
from axel.crypto.types import ArbitrageLegStatus, OneLegFailureAction


@dataclass(frozen=True)
class CrossArbCostModel:
    taker_fee_buy_bps: Decimal = Decimal("5.0")    # 0.05%
    taker_fee_sell_bps: Decimal = Decimal("5.0")   # 0.05%
    slippage_bps_leg_a: Decimal = Decimal("3.0")   # 0.03%
    slippage_bps_leg_b: Decimal = Decimal("3.0")   # 0.03%
    latency_risk_bps: Decimal = Decimal("4.0")     # 0.04% buffer for network latency
    settlement_cost_bps: Decimal = Decimal("2.0")  # 0.02%

    def total_cost_bps(self) -> Decimal:
        return (
            self.taker_fee_buy_bps
            + self.taker_fee_sell_bps
            + self.slippage_bps_leg_a
            + self.slippage_bps_leg_b
            + self.latency_risk_bps
            + self.settlement_cost_bps
        )


@dataclass(frozen=True)
class ArbitrageLegExecution:
    """Status of one individual leg in a 2-leg arbitrage trade."""

    venue: str
    symbol: str
    side: str
    requested_qty: Decimal
    filled_qty: Decimal
    price: Decimal
    status: ArbitrageLegStatus
    error: str | None = None


@dataclass(frozen=True)
class OneLegFailureIncident:
    """Incident payload generated when Leg 1 fills but Leg 2 fails."""

    strategy_id: str
    leg_a: ArbitrageLegExecution
    leg_b: ArbitrageLegExecution
    unhedged_qty: Decimal
    recommended_action: OneLegFailureAction
    timestamp: datetime
    incident_severity: str = "CRITICAL"


class OneLegFailureHandler:
    """
    Handles broken 2-leg execution without assuming simultaneous fills.
    If Leg A (e.g. Buy) fills but Leg B (Sell) fails, immediately initiates repair / hedge.
    """

    @classmethod
    def handle_failure(
        cls,
        leg_a: ArbitrageLegExecution,
        leg_b: ArbitrageLegExecution,
        now: datetime,
    ) -> OneLegFailureIncident:
        unhedged = leg_a.filled_qty - leg_b.filled_qty
        # If unhedged long position exists, immediate market hedge or unwind required
        action = OneLegFailureAction.MARKET_HEDGE if unhedged > Decimal(0) else OneLegFailureAction.CANCEL_AND_UNWIND

        return OneLegFailureIncident(
            strategy_id="crypto:cross_exchange_arbitrage",
            leg_a=leg_a,
            leg_b=leg_b,
            unhedged_qty=abs(unhedged),
            recommended_action=action,
            timestamp=now,
            incident_severity="CRITICAL",
        )


class CrossExchangeArbitrageStrategy:
    """
    Evaluates spot / perpetual price discrepancies across exchanges.
    """

    strategy_id = "crypto:cross_exchange_arbitrage"

    def __init__(self, cost_model: CrossArbCostModel | None = None) -> None:
        self.cost_model = cost_model or CrossArbCostModel()

    def evaluate_spread(
        self,
        book_buy: CryptoOrderBookRecord,
        book_sell: CryptoOrderBookRecord,
        max_skew_seconds: float = 2.0,
        min_depth_usd: Decimal = Decimal("5000.0"),
    ) -> ArbitrageSpreadOpportunity:
        """
        Evaluate if buying on book_buy and selling on book_sell provides a positive net edge.
        """
        # 1. Timestamp synchronization check
        skew = abs((book_buy.event_time - book_sell.event_time).total_seconds())
        if skew > max_skew_seconds:
            return ArbitrageSpreadOpportunity(
                symbol=book_buy.symbol,
                venue_buy=book_buy.venue,
                venue_sell=book_sell.venue,
                price_buy=book_buy.best_ask,
                price_sell=book_sell.best_bid,
                timestamp_buy=book_buy.event_time,
                timestamp_sell=book_sell.event_time,
                raw_spread_bps=Decimal(0),
                net_edge_bps=Decimal("-999.0"),
                available_liquidity_usd=Decimal(0),
                estimated_fees_bps=Decimal(0),
                estimated_slippage_bps=Decimal(0),
                is_profitable=False,
                rejection_reason=f"Timestamp skew {skew:.2f}s > threshold {max_skew_seconds}s",
            )

        # 2. Price calculation
        # We buy at the best ask of book_buy, and sell at the best bid of book_sell
        buy_price = book_buy.best_ask
        sell_price = book_sell.best_bid

        # Available liquidity is the minimum of ask depth on buy venue and bid depth on sell venue
        buy_depth = book_buy.ask_depth_usd(3)
        sell_depth = book_sell.bid_depth_usd(3)
        available_liquidity = min(buy_depth, sell_depth)

        if available_liquidity < min_depth_usd:
            return ArbitrageSpreadOpportunity(
                symbol=book_buy.symbol,
                venue_buy=book_buy.venue,
                venue_sell=book_sell.venue,
                price_buy=buy_price,
                price_sell=sell_price,
                timestamp_buy=book_buy.event_time,
                timestamp_sell=book_sell.event_time,
                raw_spread_bps=Decimal(0),
                net_edge_bps=Decimal("-999.0"),
                available_liquidity_usd=available_liquidity,
                estimated_fees_bps=Decimal(0),
                estimated_slippage_bps=Decimal(0),
                is_profitable=False,
                rejection_reason=f"Insufficient depth ${available_liquidity:,.0f} < ${min_depth_usd:,.0f}",
            )

        # Raw spread bps: (sell_price - buy_price) / buy_price * 10000
        raw_spread_bps = ((sell_price - buy_price) / buy_price) * Decimal("10000.0")
        total_costs_bps = self.cost_model.total_cost_bps()
        net_edge_bps = raw_spread_bps - total_costs_bps

        is_profitable = net_edge_bps > Decimal("0.0")
        reason = None if is_profitable else f"Net edge {net_edge_bps:.2f} bps <= 0 after realistic costs ({total_costs_bps:.2f} bps)"

        return ArbitrageSpreadOpportunity(
            symbol=book_buy.symbol,
            venue_buy=book_buy.venue,
            venue_sell=book_sell.venue,
            price_buy=buy_price,
            price_sell=sell_price,
            timestamp_buy=book_buy.event_time,
            timestamp_sell=book_sell.event_time,
            raw_spread_bps=round(raw_spread_bps, 2),
            net_edge_bps=round(net_edge_bps, 2),
            available_liquidity_usd=available_liquidity,
            estimated_fees_bps=self.cost_model.taker_fee_buy_bps + self.cost_model.taker_fee_sell_bps,
            estimated_slippage_bps=self.cost_model.slippage_bps_leg_a + self.cost_model.slippage_bps_leg_b,
            is_profitable=is_profitable,
            rejection_reason=reason,
        )
