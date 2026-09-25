"""Deterministic LLM spend guard, independent of any provider SDK."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass
class LlmBudgetGuard:
    hourly_cap_usd: float
    daily_cap_usd: float
    charges: list[tuple[datetime, float]] = field(default_factory=list)

    def charge(self, cost_usd: float, now: datetime | None = None) -> None:
        if cost_usd < 0:
            raise ValueError("cost cannot be negative")
        now = now or datetime.now(UTC)
        self.charges = [(ts, cost) for ts, cost in self.charges if ts >= now - timedelta(days=1)]
        hourly = sum(cost for ts, cost in self.charges if ts >= now - timedelta(hours=1))
        daily = sum(cost for _, cost in self.charges)
        if hourly + cost_usd > self.hourly_cap_usd:
            raise PermissionError("LLM hourly budget exceeded")
        if daily + cost_usd > self.daily_cap_usd:
            raise PermissionError("LLM daily budget exceeded")
        self.charges.append((now, cost_usd))
