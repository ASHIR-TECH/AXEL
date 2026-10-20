"""
Deterministic No-Leverage Rule enforcement for Phase 7 (Crypto).
Initial Crypto section operates strictly with zero leverage (leverage <= 1.0).
Any margin or leverage parameter must be rejected fail-closed, never ignored.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from axel.core.contracts import Order, Proposal


class NoLeverageViolationError(PermissionError):
    """Raised when any proposal, order, or setting requests leverage > 1.0 or margin."""


class NoLeverageEnforcer:
    """
    Deterministic gatekeeper rejecting any leverage or margin parameters.
    """

    @staticmethod
    def validate_leverage(leverage: Decimal | float | None) -> None:
        """
        Verify leverage parameter does not exceed 1.0.
        None is treated as 1.0 (cash spot).
        """
        if leverage is None:
            return

        val = Decimal(str(leverage))
        if val > Decimal("1.0"):
            raise NoLeverageViolationError(
                f"NO-LEVERAGE RULE VIOLATION: Requested leverage is {val} > 1.0. "
                "Phase 7 strictly forbids leverage or margin trading."
            )
        if val <= Decimal("0.0"):
            raise ValueError(f"Leverage parameter must be positive, got {val}")

    @classmethod
    def validate_proposal(cls, proposal: Proposal, extra_fields: dict[str, Any] | None = None) -> None:
        """
        Validate that proposal does not request leverage or borrowed margin.
        """
        if extra_fields:
            if "leverage" in extra_fields:
                cls.validate_leverage(extra_fields["leverage"])
            if extra_fields.get("margin"):
                raise NoLeverageViolationError(
                    "NO-LEVERAGE RULE VIOLATION: Margin parameter is enabled. "
                    "Phase 7 strictly forbids margin trading."
                )

    @classmethod
    def validate_order(cls, order: Order, leverage: Decimal | float | None = None) -> None:
        """
        Validate order submission does not request leverage.
        """
        cls.validate_leverage(leverage)
