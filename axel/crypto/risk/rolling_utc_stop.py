"""
Deterministic Rolling 24-Hour UTC Stop for Phase 7 (Crypto).

Calculates net P&L (realized + unrealized - fees) over a strict rolling 24-hour UTC window.
Breaching the stop threshold halts new Crypto risk deterministically.
Missing or corrupted data fails closed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

DEFAULT_CRYPTO_STOP_FILE = Path(".axel_crypto_stop.json")
DEFAULT_ROLLING_STOP_PCT = Decimal("0.03")  # 3.0% rolling loss limit


@dataclass(frozen=True)
class PnLEvent:
    """Historical PnL or fee event within the rolling window."""

    timestamp: datetime
    realized_pnl: Decimal
    fee_paid: Decimal = Decimal("0.0")

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("PnLEvent timestamp must be timezone-aware (UTC)")


@dataclass(frozen=True)
class RollingUtcStopState:
    """State snapshot of the rolling 24-hour stop."""

    is_halted: bool
    rolling_pnl_usd: Decimal
    rolling_loss_pct: Decimal
    threshold_pct: Decimal
    halt_reason: str | None = None
    halted_at: datetime | None = None
    rearmed_at: datetime | None = None
    rearmed_by: str | None = None


class Rolling24HourUtcStop:
    """
    Rolling 24-hour UTC loss monitor for Section.CRYPTO.
    """

    def __init__(
        self,
        stop_threshold_pct: Decimal = DEFAULT_ROLLING_STOP_PCT,
        state_file: Path = DEFAULT_CRYPTO_STOP_FILE,
    ) -> None:
        self.stop_threshold_pct = stop_threshold_pct
        self.state_file = state_file
        self._halted: bool = False
        self._halt_reason: str | None = None
        self._halted_at: datetime | None = None
        self._load_state()

    def _load_state(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self._halted = data.get("is_halted", False)
                self._halt_reason = data.get("halt_reason")
                halted_at_str = data.get("halted_at")
                self._halted_at = datetime.fromisoformat(halted_at_str) if halted_at_str else None
            except (json.JSONDecodeError, KeyError, ValueError, OSError):
                # Corrupted state fails closed
                self._halted = True
                self._halt_reason = "Corrupted rolling stop state file — fail closed"
                self._halted_at = datetime.now(UTC)

    def _save_state(self) -> None:
        payload = {
            "is_halted": self._halted,
            "halt_reason": self._halt_reason,
            "halted_at": self._halted_at.isoformat() if self._halted_at else None,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self.state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def trip_halt(self, reason: str, now: datetime | None = None) -> None:
        self._halted = True
        self._halt_reason = reason
        self._halted_at = now or datetime.now(UTC)
        self._save_state()

    def re_arm(self, operator_token: str, rationale: str) -> None:
        if not operator_token or not rationale.strip():
            raise PermissionError("Re-arming the Crypto rolling stop requires an operator token and written rationale")
        self._halted = False
        self._halt_reason = None
        self._halted_at = None
        self._save_state()

    def evaluate(
        self,
        reference_equity: Decimal,
        unrealized_pnl: Decimal,
        pnl_events: Sequence[PnLEvent] | None,
        now: datetime | None = None,
    ) -> RollingUtcStopState:
        """
        Evaluate P&L over the rolling 24-hour UTC window.
        Fails closed on missing or non-positive reference equity.
        """
        now = now or datetime.now(UTC)

        # Fail closed on missing data
        if reference_equity <= Decimal("0.0"):
            self.trip_halt("Missing or non-positive reference equity", now)
            return RollingUtcStopState(
                is_halted=True,
                rolling_pnl_usd=Decimal("0.0"),
                rolling_loss_pct=Decimal("-1.0"),
                threshold_pct=-self.stop_threshold_pct,
                halt_reason=self._halt_reason,
                halted_at=self._halted_at,
            )

        if pnl_events is None:
            self.trip_halt("Missing PnL event history", now)
            return RollingUtcStopState(
                is_halted=True,
                rolling_pnl_usd=Decimal("0.0"),
                rolling_loss_pct=Decimal("-1.0"),
                threshold_pct=-self.stop_threshold_pct,
                halt_reason=self._halt_reason,
                halted_at=self._halted_at,
            )

        if self._halted:
            return RollingUtcStopState(
                is_halted=True,
                rolling_pnl_usd=Decimal("0.0"),
                rolling_loss_pct=Decimal("0.0"),
                threshold_pct=-self.stop_threshold_pct,
                halt_reason=self._halt_reason,
                halted_at=self._halted_at,
            )

        cutoff = now - timedelta(hours=24)
        window_events = [e for e in pnl_events if e.timestamp >= cutoff]

        realized = sum((e.realized_pnl for e in window_events), Decimal("0.0"))
        fees = sum((e.fee_paid for e in window_events), Decimal("0.0"))
        net_rolling_pnl = realized + unrealized_pnl - fees
        loss_pct = net_rolling_pnl / reference_equity
        threshold = -abs(self.stop_threshold_pct)

        if loss_pct <= threshold:
            reason = (
                f"ROLLING_24H_UTC_STOP_BREACHED: Net loss {loss_pct:.2%} <= threshold {threshold:.2%} "
                f"(Net PnL: ${net_rolling_pnl:,.2f} on equity ${reference_equity:,.2f})."
            )
            self.trip_halt(reason, now)
            return RollingUtcStopState(
                is_halted=True,
                rolling_pnl_usd=net_rolling_pnl,
                rolling_loss_pct=round(loss_pct, 4),
                threshold_pct=threshold,
                halt_reason=reason,
                halted_at=now,
            )

        return RollingUtcStopState(
            is_halted=False,
            rolling_pnl_usd=net_rolling_pnl,
            rolling_loss_pct=round(loss_pct, 4),
            threshold_pct=threshold,
            halt_reason=None,
            halted_at=None,
        )
