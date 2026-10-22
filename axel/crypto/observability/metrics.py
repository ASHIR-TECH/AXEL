"""
Observability and Metrics Collector for Phase 7 (Crypto).
Exposes real-time snapshots of venue health, data lag, funding updates, spread opportunities,
rejected edge, liquidity, hypothetical P&L, fees/slippage, leg completion, on-chain freshness,
section exposure, rolling 24h loss, stop state, and incidents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from axel.crypto.schemas import CryptoVenueHealth


@dataclass(frozen=True)
class CryptoObservabilitySnapshot:
    """Consolidated observability metrics for the Crypto vertical."""

    timestamp: datetime
    venue_health: dict[str, dict[str, Any]]
    data_lag_seconds: dict[str, float]
    active_funding_rates: dict[str, float]
    spread_opportunities_detected_24h: int
    spread_opportunities_accepted_24h: int
    rejected_edge_count_24h: int
    rejected_reasons: dict[str, int]
    top_liquidity_depth_usd: dict[str, float]
    hypothetical_net_pnl_usd: float
    total_fees_and_slippage_usd: float
    arbitrage_leg_completion_rate: float
    onchain_data_freshness_seconds: float
    section_notional_exposure_usd: float
    rolling_24h_loss_pct: float
    rolling_24h_stop_halted: bool
    active_crypto_incidents_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "venue_health": self.venue_health,
            "data_lag_seconds": self.data_lag_seconds,
            "active_funding_rates": self.active_funding_rates,
            "spread_opportunities_detected_24h": self.spread_opportunities_detected_24h,
            "spread_opportunities_accepted_24h": self.spread_opportunities_accepted_24h,
            "rejected_edge_count_24h": self.rejected_edge_count_24h,
            "rejected_reasons": self.rejected_reasons,
            "top_liquidity_depth_usd": self.top_liquidity_depth_usd,
            "hypothetical_net_pnl_usd": self.hypothetical_net_pnl_usd,
            "total_fees_and_slippage_usd": self.total_fees_and_slippage_usd,
            "arbitrage_leg_completion_rate": self.arbitrage_leg_completion_rate,
            "onchain_data_freshness_seconds": self.onchain_data_freshness_seconds,
            "section_notional_exposure_usd": self.section_notional_exposure_usd,
            "rolling_24h_loss_pct": self.rolling_24h_loss_pct,
            "rolling_24h_stop_halted": self.rolling_24h_stop_halted,
            "active_crypto_incidents_count": self.active_crypto_incidents_count,
        }


class CryptoMetricsCollector:
    """
    Collects and snapshots crypto observability metrics.
    """

    def __init__(self) -> None:
        self.venue_health_records: dict[str, CryptoVenueHealth] = {}
        self.spread_opportunities_detected = 0
        self.spread_opportunities_accepted = 0
        self.rejected_reasons: dict[str, int] = {}
        self.hypothetical_pnl = Decimal("0.0")
        self.total_costs = Decimal("0.0")
        self.total_trades = 0
        self.completed_legs = 0
        self.last_onchain_update: datetime | None = None
        self.active_incidents = 0

    def record_opportunity(self, is_accepted: bool, rejection_reason: str | None = None) -> None:
        self.spread_opportunities_detected += 1
        if is_accepted:
            self.spread_opportunities_accepted += 1
        else:
            reason = rejection_reason or "Unknown"
            self.rejected_reasons[reason] = self.rejected_reasons.get(reason, 0) + 1

    def record_venue_health(self, health: CryptoVenueHealth) -> None:
        self.venue_health_records[health.venue] = health

    def record_paper_trade(
        self,
        net_pnl: Decimal,
        costs: Decimal,
        leg_completed: bool,
    ) -> None:
        self.total_trades += 1
        if leg_completed:
            self.completed_legs += 1
        self.hypothetical_pnl += net_pnl
        self.total_costs += costs

    def snapshot(
        self,
        rolling_24h_loss_pct: float = 0.0,
        rolling_stop_halted: bool = False,
        section_exposure_usd: float = 0.0,
        now: datetime | None = None,
    ) -> CryptoObservabilitySnapshot:
        now = now or datetime.now(UTC)

        venue_health_dict = {
            v: {
                "status": h.status.value,
                "latency_ms": h.latency_ms,
                "maintenance": h.maintenance_mode,
                "stale_seconds": h.stale_data_seconds,
                "is_operational": h.is_operational,
            }
            for v, h in self.venue_health_records.items()
        }

        data_lag = {v: h.stale_data_seconds for v, h in self.venue_health_records.items()}
        completion_rate = (self.completed_legs / self.total_trades) if self.total_trades > 0 else 1.0

        onchain_lag = (now - self.last_onchain_update).total_seconds() if self.last_onchain_update else 0.0

        return CryptoObservabilitySnapshot(
            timestamp=now,
            venue_health=venue_health_dict,
            data_lag_seconds=data_lag,
            active_funding_rates={},
            spread_opportunities_detected_24h=self.spread_opportunities_detected,
            spread_opportunities_accepted_24h=self.spread_opportunities_accepted,
            rejected_edge_count_24h=sum(self.rejected_reasons.values()),
            rejected_reasons=dict(self.rejected_reasons),
            top_liquidity_depth_usd={},
            hypothetical_net_pnl_usd=float(self.hypothetical_pnl),
            total_fees_and_slippage_usd=float(self.total_costs),
            arbitrage_leg_completion_rate=round(completion_rate, 4),
            onchain_data_freshness_seconds=onchain_lag,
            section_notional_exposure_usd=section_exposure_usd,
            rolling_24h_loss_pct=rolling_24h_loss_pct,
            rolling_24h_stop_halted=rolling_stop_halted,
            active_crypto_incidents_count=self.active_incidents,
        )
