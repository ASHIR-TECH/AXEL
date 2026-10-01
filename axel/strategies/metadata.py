"""Strategy identity, versioning and lifecycle states."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256


class StrategyStatus(StrEnum):
    DRAFT = "draft"
    BACKTESTED = "backtested"
    VALIDATED = "validated"
    PAPER_ELIGIBLE = "paper_eligible"
    ACTIVE = "active"
    REJECTED = "rejected"
    RETIRED = "retired"


@dataclass(frozen=True)
class StrategyMetadata:
    strategy_id: str
    name: str
    version: str
    description: str = ""
    params: tuple[tuple[str, str], ...] = ()
    status: StrategyStatus = StrategyStatus.DRAFT

    def __post_init__(self) -> None:
        if not (self.strategy_id and self.name and self.version):
            raise ValueError("strategy metadata requires id, name and version")
        if not isinstance(self.status, StrategyStatus):
            raise TypeError("status must be a StrategyStatus")

    def fingerprint(self) -> str:
        payload = f"{self.strategy_id}|{self.version}|{sorted(self.params)}"
        return sha256(payload.encode("utf-8")).hexdigest()

    def with_status(self, status: StrategyStatus) -> StrategyMetadata:
        return replace(self, status=status)
