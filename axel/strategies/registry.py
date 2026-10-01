"""Strategy registry enforcing the Phase 2 lifecycle.

Phase 2 is a research/validation substrate: it can move a strategy through
BACKTESTED -> VALIDATED -> PAPER_ELIGIBLE, but it never grants live broker
authority (``can_place_live_orders`` is always ``False``).
"""

from __future__ import annotations

from axel.strategies.metadata import StrategyMetadata, StrategyStatus

ALLOWED_TRANSITIONS: dict[StrategyStatus, frozenset[StrategyStatus]] = {
    StrategyStatus.DRAFT: frozenset({StrategyStatus.BACKTESTED}),
    StrategyStatus.BACKTESTED: frozenset(
        {StrategyStatus.VALIDATED, StrategyStatus.REJECTED}
    ),
    StrategyStatus.VALIDATED: frozenset(
        {StrategyStatus.PAPER_ELIGIBLE, StrategyStatus.REJECTED}
    ),
    StrategyStatus.PAPER_ELIGIBLE: frozenset(
        {StrategyStatus.ACTIVE, StrategyStatus.RETIRED, StrategyStatus.REJECTED}
    ),
    StrategyStatus.ACTIVE: frozenset({StrategyStatus.RETIRED}),
    StrategyStatus.REJECTED: frozenset({StrategyStatus.RETIRED}),
    StrategyStatus.RETIRED: frozenset(),
}


class StrategyRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, StrategyMetadata] = {}

    def register(self, metadata: StrategyMetadata) -> StrategyMetadata:
        if metadata.strategy_id in self._entries:
            raise ValueError(f"strategy already registered: {metadata.strategy_id}")
        self._entries[metadata.strategy_id] = metadata
        return metadata

    def get(self, strategy_id: str) -> StrategyMetadata:
        try:
            return self._entries[strategy_id]
        except KeyError as exc:
            raise KeyError(f"unknown strategy: {strategy_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def all(self) -> list[StrategyMetadata]:
        return list(self._entries.values())

    def by_status(self, status: StrategyStatus) -> list[StrategyMetadata]:
        return [meta for meta in self._entries.values() if meta.status == status]

    def transition(self, strategy_id: str, status: StrategyStatus) -> StrategyMetadata:
        current = self.get(strategy_id)
        if status not in ALLOWED_TRANSITIONS[current.status]:
            raise ValueError(
                f"illegal transition {current.status.value} -> {status.value}"
                f" for {strategy_id}"
            )
        updated = current.with_status(status)
        self._entries[strategy_id] = updated
        return updated

    def require_status(self, strategy_id: str, status: StrategyStatus) -> StrategyMetadata:
        metadata = self.get(strategy_id)
        if metadata.status != status:
            raise ValueError(
                f"{strategy_id} is {metadata.status.value}, expected {status.value}"
            )
        return metadata

    def can_place_live_orders(self, strategy_id: str) -> bool:
        """Phase 2 never confers live execution authority."""
        self.get(strategy_id)
        return False

    def __len__(self) -> int:
        return len(self._entries)