"""
Execution-authority guards (Phase 6, spec §14).

Agents propose; they never place or cancel broker orders. These guards make that
structural: an :class:`~axel.execution.approval.AuthenticatedPrincipal` of kind
``AGENT`` is refused access to the execution (submit/cancel) surface regardless
of what it claims from a client.

The guard is deliberately fail-closed for structural agent kinds but tolerant of
``None`` so the internal execution loop (which runs as the execution service, not
an agent) keeps working.
"""

from __future__ import annotations

from axel.execution.approval import AuthenticatedPrincipal, PrincipalKind

# Kinds permitted to reach the execution surface.
_EXECUTION_ALLOWED_KINDS = frozenset({PrincipalKind.HUMAN, PrincipalKind.SERVICE})


def assert_may_submit(principal: AuthenticatedPrincipal | None) -> None:
    """Refuse order submission by an agent principal."""
    if principal is not None and principal.kind not in _EXECUTION_ALLOWED_KINDS:
        raise PermissionError(
            f"Principal {principal.principal_id!r} of kind {principal.kind.value!r} "
            "is not permitted to submit orders."
        )


def assert_may_cancel(principal: AuthenticatedPrincipal | None) -> None:
    """Refuse order cancellation by an agent principal."""
    if principal is not None and principal.kind not in _EXECUTION_ALLOWED_KINDS:
        raise PermissionError(
            f"Principal {principal.principal_id!r} of kind {principal.kind.value!r} "
            "is not permitted to cancel orders."
        )
