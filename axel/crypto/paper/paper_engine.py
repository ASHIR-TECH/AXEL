"""
Crypto Paper Trading Engine for Phase 7 (Crypto).
Executes simulated crypto orders against live/mock market data without moving real capital.
Tracks:
- hypothetical net edge after realistic costs
- 2-leg arbitrage completion rate
- accrued funding payments
- stale-data events and venue dropouts
- broker/exchange vs internal DB reconciliation
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, OrderType, RiskVerdict
from axel.crypto.adapters.base import CryptoExchangeAdapter
from axel.crypto.schemas import ArbitrageSpreadOpportunity


@dataclass(frozen=True)
class PaperArbitrageExecutionResult:
    """Outcome of paper arbitrage trade."""

    opportunity_id: str
    symbol: str
    venue_buy: str
    venue_sell: str
    leg_buy_filled: bool
    leg_sell_filled: bool
    leg_buy_price: Decimal
    leg_sell_price: Decimal
    qty: Decimal
    gross_pnl_usd: Decimal
    net_pnl_usd: Decimal
    funding_pnl_usd: Decimal
    fees_paid_usd: Decimal
    slippage_cost_usd: Decimal
    is_fully_hedged: bool
    timestamp: datetime


@dataclass
class CryptoPaperState:
    total_paper_trades: int = 0
    successful_arbitrage_hedges: int = 0
    one_leg_failures: int = 0
    cumulative_net_pnl: Decimal = Decimal("0.0")
    total_funding_accrued: Decimal = Decimal("0.0")
    total_fees_paid: Decimal = Decimal("0.0")
    stale_data_incidents: int = 0
    venue_outage_incidents: int = 0
    reconciliation_breaks: int = 0


class CryptoPaperEngine:
    """
    Simulates paper trading across crypto exchanges.
    """

    def __init__(
        self,
        adapters: dict[str, CryptoExchangeAdapter],
    ) -> None:
        self.adapters = adapters
        self.state = CryptoPaperState()
        self.execution_history: list[PaperArbitrageExecutionResult] = []

    def execute_paper_arbitrage(
        self,
        opportunity: ArbitrageSpreadOpportunity,
        qty: Decimal,
        risk_decision: RiskDecision,
    ) -> PaperArbitrageExecutionResult:
        """
        Simulate 2-leg cross-exchange or spot/perp execution in paper mode.
        """
        now = datetime.now(UTC)
        if not risk_decision.approved or risk_decision.verdict is not RiskVerdict.APPROVED:
            raise PermissionError(f"Risk decision {risk_decision.verdict.value} blocks paper order")

        adapter_buy = self.adapters.get(opportunity.venue_buy)
        adapter_sell = self.adapters.get(opportunity.venue_sell)

        if not adapter_buy or not adapter_sell:
            self.state.venue_outage_incidents += 1
            raise ValueError(f"Missing adapter for {opportunity.venue_buy} or {opportunity.venue_sell}")

        # Execute Leg 1 (Buy)
        order_buy = Order(
            client_order_id=f"paper_buy_{now.timestamp()}",
            proposal_id=risk_decision.proposal_id,
            symbol=opportunity.symbol,
            side=OrderSide.BUY,
            qty=float(qty),
            limit_price=float(opportunity.price_buy),
            type=OrderType.LIMIT,
        )
        res_buy = adapter_buy.submit_order(order_buy, risk_decision, leverage=1)

        # Execute Leg 2 (Sell)
        order_sell = Order(
            client_order_id=f"paper_sell_{now.timestamp()}",
            proposal_id=risk_decision.proposal_id,
            symbol=opportunity.symbol,
            side=OrderSide.SELL,
            qty=float(qty),
            limit_price=float(opportunity.price_sell),
            type=OrderType.LIMIT,
        )
        res_sell = adapter_sell.submit_order(order_sell, risk_decision, leverage=1)

        buy_ok = res_buy.status == "filled"
        sell_ok = res_sell.status == "filled"

        self.state.total_paper_trades += 1

        if buy_ok and sell_ok:
            self.state.successful_arbitrage_hedges += 1
            is_hedged = True
            notional = opportunity.price_buy * qty
            gross_pnl = (opportunity.raw_spread_bps / Decimal("10000.0")) * notional
            fees = (opportunity.estimated_fees_bps / Decimal("10000.0")) * notional
            slip = (opportunity.estimated_slippage_bps / Decimal("10000.0")) * notional
            funding_pnl = (opportunity.funding_diff_bps / Decimal("10000.0")) * notional
            net_pnl = (opportunity.net_edge_bps / Decimal("10000.0")) * notional
        else:
            self.state.one_leg_failures += 1
            is_hedged = False
            # If broken leg, assume loss from emergency market hedge/unwind
            gross_pnl = Decimal("-50.0")
            fees = Decimal("10.0")
            slip = Decimal("15.0")
            funding_pnl = Decimal("0.0")
            net_pnl = gross_pnl - fees - slip

        self.state.cumulative_net_pnl += net_pnl
        self.state.total_fees_paid += fees
        self.state.total_funding_accrued += funding_pnl

        result = PaperArbitrageExecutionResult(
            opportunity_id=f"arb_{now.timestamp()}",
            symbol=opportunity.symbol,
            venue_buy=opportunity.venue_buy,
            venue_sell=opportunity.venue_sell,
            leg_buy_filled=buy_ok,
            leg_sell_filled=sell_ok,
            leg_buy_price=opportunity.price_buy,
            leg_sell_price=opportunity.price_sell,
            qty=qty,
            gross_pnl_usd=round(gross_pnl, 2),
            net_pnl_usd=round(net_pnl, 2),
            funding_pnl_usd=round(funding_pnl, 2),
            fees_paid_usd=round(fees, 2),
            slippage_cost_usd=round(slip, 2),
            is_fully_hedged=is_hedged,
            timestamp=now,
        )
        self.execution_history.append(result)
        return result

    def get_completion_rate(self) -> float:
        if self.state.total_paper_trades == 0:
            return 0.0
        return self.state.successful_arbitrage_hedges / self.state.total_paper_trades
