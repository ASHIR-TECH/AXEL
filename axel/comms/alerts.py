"""Side-effect-free alert payload construction; transport is configured outside AXEL core."""

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Alert:
    kind: str
    message: str
    created_at: datetime
    severity: str = "warning"


def halt_alert(reason: str) -> Alert:
    return Alert(kind="halt", severity="critical", message=f"AXEL HALT: {reason}", created_at=datetime.now(UTC))
