"""
Independent Venue Risk & Circuit Breaker for Phase 7 (Crypto).
Tracks venue health, latency, rejection spikes, maintenance mode, and data freshness.
Allows disabling a single venue independently without halting the entire system.
"""

from __future__ import annotations

from dataclasses import dataclass

from axel.crypto.schemas import CryptoVenueHealth
from axel.crypto.types import VenueStatus


@dataclass(frozen=True)
class VenueRiskLimits:
    max_latency_ms: float = 500.0
    max_stale_seconds: float = 15.0
    max_rejection_rate: float = 0.05  # 5%
    auto_disable_on_maintenance: bool = True


class VenueRiskManager:
    """
    Manages per-venue operational risk and circuit breaking.
    """

    def __init__(self, limits: VenueRiskLimits | None = None) -> None:
        self.limits = limits or VenueRiskLimits()
        self._disabled_venues: dict[str, str] = {}  # venue -> reason
        self._venue_health: dict[str, CryptoVenueHealth] = {}

    def update_health(self, health: CryptoVenueHealth) -> None:
        self._venue_health[health.venue] = health

        # Automated venue circuit breaker
        if health.maintenance_mode and self.limits.auto_disable_on_maintenance:
            self.disable_venue(health.venue, "Venue entered maintenance mode")
        elif health.latency_ms > self.limits.max_latency_ms:
            self.disable_venue(health.venue, f"Excessive API latency: {health.latency_ms}ms")
        elif health.stale_data_seconds > self.limits.max_stale_seconds:
            self.disable_venue(health.venue, f"Stale market data: {health.stale_data_seconds}s")
        elif health.rejection_rate > self.limits.max_rejection_rate:
            self.disable_venue(health.venue, f"High order rejection rate: {health.rejection_rate:.1%}")
        elif not health.withdrawal_status_ok:
            self.disable_venue(health.venue, "Venue withdrawals suspended / impaired")

    def disable_venue(self, venue: str, reason: str) -> None:
        self._disabled_venues[venue] = reason

    def enable_venue(self, venue: str) -> None:
        self._disabled_venues.pop(venue, None)

    def is_venue_allowed(self, venue: str) -> tuple[bool, str | None]:
        if venue in self._disabled_venues:
            return False, f"Venue '{venue}' is disabled: {self._disabled_venues[venue]}"
        if venue in self._venue_health:
            health = self._venue_health[venue]
            if health.status in (VenueStatus.HALTED, VenueStatus.MAINTENANCE):
                return False, f"Venue '{venue}' status is {health.status.value}"
        return True, None

    def get_disabled_venues(self) -> dict[str, str]:
        return dict(self._disabled_venues)
