"""
Axel Prime Overseer (LLM Half) for Phase 3.
Evaluates macroeconomic context and relative section performance.
Emits typed AllocationProposal records ONLY.
Has ZERO broker credentials or direct execution access.
"""

from __future__ import annotations

from datetime import UTC, datetime

from axel.core.contracts import AllocationProposal
from axel.core.types import Section


class AxelOverseer:
    """
    High-level agent overseer evaluating capital distribution across sections.
    Emits only AllocationProposal records crossing the trust boundary.
    """

    name = "axel_prime_overseer"

    def propose_reallocation(
        self,
        from_section: Section,
        to_section: Section,
        pct_of_total: float,
        rationale: str,
    ) -> AllocationProposal:
        """
        Propose capital shift across sections.
        Subject to deterministic clamping by CapitalAllocator (max 5% per cycle).
        """
        if from_section == to_section:
            raise ValueError("from_section and to_section must be different")

        if pct_of_total <= 0.0 or pct_of_total > 1.0:
            raise ValueError("pct_of_total must be between 0.0 and 1.0")

        return AllocationProposal(
            from_section=from_section,
            to_section=to_section,
            pct_of_total=pct_of_total,
            rationale=f"AxelOverseer: {rationale}",
            ts=datetime.now(UTC),
        )
