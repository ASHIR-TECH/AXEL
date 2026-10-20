"""
Prompt and model audit records for Phase 3 Agent Layer.
Maintains an immutable audit trail of prompt templates, model versions, token counts, and costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256


@dataclass(frozen=True)
class PromptAuditRecord:
    """Immutable record tracking one LLM prompt/completion cycle."""

    prompt_version: str
    prompt_template_id: str
    model: str
    provider: str
    system_prompt_hash: str
    user_input_hash: str
    response_hash: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    latency_ms: int
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def key(self) -> str:
        return f"audit:{self.prompt_template_id}:{self.timestamp.isoformat()}"

    def fingerprint(self) -> str:
        payload = (
            f"{self.prompt_version}:{self.prompt_template_id}:{self.model}:"
            f"{self.system_prompt_hash}:{self.user_input_hash}:{self.response_hash}"
        )
        return sha256(payload.encode("utf-8")).hexdigest()


class PromptAuditLog:
    """In-memory append-only log of prompt audits."""

    def __init__(self) -> None:
        self._records: list[PromptAuditRecord] = []

    def record(self, audit: PromptAuditRecord) -> None:
        self._records.append(audit)

    def total_cost_usd(self) -> Decimal:
        return sum((r.cost_usd for r in self._records), Decimal("0.0"))

    def total_tokens(self) -> int:
        return sum((r.prompt_tokens + r.completion_tokens for r in self._records), 0)

    def count(self) -> int:
        return len(self._records)

    def all_records(self) -> tuple[PromptAuditRecord, ...]:
        return tuple(self._records)
