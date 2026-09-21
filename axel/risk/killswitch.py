"""
Global circuit breaker and kill switch mechanism for AXEL.
Survives process restarts and enforces manual operator intervention with written reasoning.
"""

import hashlib
import json
from pathlib import Path

from axel.core.clock import Clock, RealClock
from axel.core.config import settings
from axel.core.logging import logger
from axel.risk.limits import LIMITS, RiskLimits


class KillSwitch:
    """
    Monitors global peak-to-trough portfolio equity drawdown.
    Triggers an immediate hard HALT when drawdown breaches 10%.
    Once triggered, ALL order execution is blocked until explicitly re-armed.
    """

    def __init__(
        self,
        state_file_path: Path | None = None,
        limits: RiskLimits = LIMITS,
        clock: Clock | None = None,
    ):
        self.limits = limits
        self.clock = clock or RealClock()
        self.state_file = state_file_path or Path(".axel_killswitch.json")
        self._halted: bool = False
        self._halt_reason: str | None = None
        self._halted_at: str | None = None
        self._high_water_mark: float = 0.0
        self._load_state()

    def _load_state(self) -> None:
        """Load persisted circuit-breaker state from disk."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self._halted = data.get("halted", False)
                self._halt_reason = data.get("reason")
                self._halted_at = data.get("halted_at")
                self._high_water_mark = data.get("high_water_mark", 0.0)
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Failed to load kill switch state file", extra={"error": str(e)})

    def _save_state(self) -> None:
        """Persist state to survive crashes or process restarts."""
        data = {
            "halted": self._halted,
            "reason": self._halt_reason,
            "halted_at": self._halted_at,
            "high_water_mark": self._high_water_mark,
            "updated_at": self.clock.now().isoformat(),
        }
        try:
            self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            logger.error("Failed to persist kill switch state", extra={"error": str(e)})

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str | None:
        return self._halt_reason

    @property
    def high_water_mark(self) -> float:
        return self._high_water_mark

    def set_high_water_mark(self, value: float) -> None:
        """Update reference peak equity."""
        if value > self._high_water_mark:
            self._high_water_mark = value
            self._save_state()

    def check_equity(self, current_equity: float) -> tuple[bool, float]:
        """
        Calculates peak-to-trough drawdown against high water mark.
        Returns: (is_halted, current_drawdown_pct)
        """
        if current_equity <= 0.0:
            return self._halted, 0.0

        if current_equity > self._high_water_mark:
            self._high_water_mark = current_equity
            self._save_state()
            return False, 0.0

        if self._high_water_mark <= 0.0:
            self._high_water_mark = current_equity
            self._save_state()
            return False, 0.0

        drawdown = (self._high_water_mark - current_equity) / self._high_water_mark

        if drawdown >= self.limits.GLOBAL_MAX_DRAWDOWN:
            reason = (
                f"GLOBAL DRAWDOWN BREACH: Current drawdown {drawdown:.2%} >= "
                f"limit {self.limits.GLOBAL_MAX_DRAWDOWN:.2%}. "
                f"Peak: ${self._high_water_mark:,.2f}, Current: ${current_equity:,.2f}."
            )
            self.trip(reason=reason)
            return True, drawdown

        return self._halted, drawdown

    def trip(self, reason: str, section: str | None = None) -> None:
        """Activates the kill switch and persists the lock."""
        self._halted = True
        self._halt_reason = reason
        self._halted_at = self.clock.now().isoformat()
        self._save_state()
        logger.critical(
            "KILL SWITCH TRIPPED: All automated executions HALTED.",
            extra={"reason": reason, "section": section, "timestamp": self._halted_at},
        )

    def re_arm(self, operator_token: str, written_rationale: str, new_equity_baseline: float) -> None:
        """
        Re-arms the system.
        Requires the correct operator token and a written justification (>= 10 chars).
        """
        expected_secret = settings.operator_rearm_secret.get_secret_value()
        # Compare securely using sha256 digests
        provided_digest = hashlib.sha256(operator_token.encode("utf-8")).hexdigest()
        expected_digest = hashlib.sha256(expected_secret.encode("utf-8")).hexdigest()

        if provided_digest != expected_digest:
            logger.warning("Failed re-arm attempt: Invalid operator token.")
            raise PermissionError("Access denied: Invalid operator token provided.")

        if not written_rationale or len(written_rationale.strip()) < 10:
            raise ValueError("Re-arming requires an auditable written rationale (minimum 10 characters).")

        self._halted = False
        self._halt_reason = None
        self._halted_at = None
        self._high_water_mark = max(new_equity_baseline, 1.0)
        self._save_state()

        logger.info(
            "KILL SWITCH RE-ARMED by operator.",
            extra={
                "rationale": written_rationale,
                "new_equity_baseline": new_equity_baseline,
                "timestamp": self.clock.now().isoformat(),
            },
        )
