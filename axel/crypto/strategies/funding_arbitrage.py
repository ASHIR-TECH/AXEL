"""
Funding-Rate Arbitrage Strategy for Phase 7 (Crypto).

Identifies funding differentials between spot and perpetual or across venues.
Strict Rule: Evaluates net edge AFTER realistic costs:
  funding payment - (maker/taker fees + spread + slippage + latency + transfer/settlement + venue risk haircut).
Raw spreads with non-positive net edge are strictly rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from axel.crypto.schemas import ArbitrageSpreadOpportunity, FundingRateRecord


@dataclass(frozen=True)
class FundingArbCostModel:
    taker_fee_bps: Decimal = Decimal("5.0")       # 0.05%
    maker_fee_bps: Decimal = Decimal("2.0")       # 0.02%
    half_spread_bps: Decimal = Decimal("2.5")     # 0.025%
    slippage_bps: Decimal = Decimal("2.0")        # 0.02%
    settlement_cost_bps: Decimal = Decimal("1.0") # 0.01%
    venue_risk_haircut_bps: Decimal = Decimal("3.0") # 0.03% haircut for counterparty/venue risk

    def round_trip_cost_bps(self) -> Decimal:
        """
        Total entry and exit cost across both legs (Spot + Perp or Perp A + Perp B).
        Entry: 2 * (fee + spread + slippage)
        Exit: 2 * (fee + spread + slippage)
        Plus settlement and venue risk.
        """
        leg_cost = self.taker_fee_bps + self.half_spread_bps + self.slippage_bps
        # Round trip for 2 legs (open 2 legs, close 2 legs = 4 executions)
        return (leg_cost * Decimal("4.0")) + self.settlement_cost_bps + self.venue_risk_haircut_bps


class FundingArbitrageStrategy:
    """
    Evaluates perpetual funding rate arbitrage opportunities.
    """

    strategy_id = "crypto:funding_arbitrage"

    def __init__(self, cost_model: FundingArbCostModel | None = None) -> None:
        self.cost_model = cost_model or FundingArbCostModel()

    def evaluate_spread(
        self,
        funding_a: FundingRateRecord,
        funding_b: FundingRateRecord,
        available_liquidity_usd: Decimal,
        min_liquidity_usd: Decimal = Decimal("10000.0"),
    ) -> ArbitrageSpreadOpportunity:
        """
        Evaluate funding rate differential across two venues.
        Synchronizes timestamps and applies full cost model.
        """
        # Timestamp synchronization check (must be within 60 seconds)
        skew = abs((funding_a.event_time - funding_b.event_time).total_seconds())
        if skew > 60.0:
            return ArbitrageSpreadOpportunity(
                symbol=funding_a.symbol,
                venue_buy=funding_a.venue,
                venue_sell=funding_b.venue,
                price_buy=funding_a.mark_price or Decimal(0),
                price_sell=funding_b.mark_price or Decimal(0),
                timestamp_buy=funding_a.event_time,
                timestamp_sell=funding_b.event_time,
                raw_spread_bps=Decimal(0),
                net_edge_bps=Decimal("-999.0"),
                available_liquidity_usd=available_liquidity_usd,
                estimated_fees_bps=Decimal(0),
                estimated_slippage_bps=Decimal(0),
                is_profitable=False,
                rejection_reason=f"Timestamp skew {skew:.1f}s exceeds max allowed 60s",
            )

        # Liquidity check
        if available_liquidity_usd < min_liquidity_usd:
            return ArbitrageSpreadOpportunity(
                symbol=funding_a.symbol,
                venue_buy=funding_a.venue,
                venue_sell=funding_b.venue,
                price_buy=funding_a.mark_price or Decimal(0),
                price_sell=funding_b.mark_price or Decimal(0),
                timestamp_buy=funding_a.event_time,
                timestamp_sell=funding_b.event_time,
                raw_spread_bps=Decimal(0),
                net_edge_bps=Decimal("-999.0"),
                available_liquidity_usd=available_liquidity_usd,
                estimated_fees_bps=Decimal(0),
                estimated_slippage_bps=Decimal(0),
                is_profitable=False,
                rejection_reason=f"Insufficient liquidity: ${available_liquidity_usd:,.0f} < ${min_liquidity_usd:,.0f}",
            )

        # Funding rate differential (e.g. Venue B pays 0.10%, Venue A pays 0.01% -> diff = 0.09% = 9 bps)
        # Annualized or per-cycle basis: 3 cycles per day (8h intervals)
        rate_diff = funding_b.rate - funding_a.rate
        raw_spread_bps = rate_diff * Decimal("10000.0")

        # Annualized expected funding edge over 3 funding cycles (1 day)
        expected_funding_bps = raw_spread_bps * Decimal("3.0")
        total_costs_bps = self.cost_model.round_trip_cost_bps()
        net_edge_bps = expected_funding_bps - total_costs_bps

        is_profitable = net_edge_bps > Decimal("0.0")
        reason = None if is_profitable else f"Net edge {net_edge_bps:.2f} bps <= 0 after costs ({total_costs_bps:.2f} bps)"

        return ArbitrageSpreadOpportunity(
            symbol=funding_a.symbol,
            venue_buy=funding_a.venue,
            venue_sell=funding_b.venue,
            price_buy=funding_a.mark_price or Decimal(0),
            price_sell=funding_b.mark_price or Decimal(0),
            timestamp_buy=funding_a.event_time,
            timestamp_sell=funding_b.event_time,
            raw_spread_bps=round(raw_spread_bps, 2),
            net_edge_bps=round(net_edge_bps, 2),
            available_liquidity_usd=available_liquidity_usd,
            estimated_fees_bps=self.cost_model.taker_fee_bps * Decimal("4.0"),
            estimated_slippage_bps=self.cost_model.slippage_bps * Decimal("4.0"),
            funding_diff_bps=round(raw_spread_bps, 2),
            venue_risk_haircut_bps=self.cost_model.venue_risk_haircut_bps,
            is_profitable=is_profitable,
            rejection_reason=reason,
        )
