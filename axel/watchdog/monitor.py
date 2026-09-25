"""
Independent Watchdog process monitor.
Periodically checks portfolio equity drawdown, heartbeat freshness, and fires HALT when breached.

alert_callback receives Alert objects from axel.comms.alerts. The callback signature accepts
Any to avoid importing the comms layer here (keeping this module lean and testable without comms).
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from axel.comms.alerts import halt_alert, heartbeat_alert, stale_data_alert
from axel.core.clock import Clock, RealClock
from axel.core.logging import logger
from axel.execution.broker_base import BrokerAdapter
from axel.risk.killswitch import KillSwitch
from axel.risk.limits import LIMITS, RiskLimits


class WatchdogMonitor:
    """
    Independent watchdog process runner.
    Monitors high-water mark drawdown and sets global HALT if drawdown exceeds 10%.

    alert_callback receives Alert objects (axel.comms.alerts.Alert).
    It is typed as Callable[[Any], None] so callers may pass a TelegramTransport.send
    or any other delivery function without creating an import dependency here.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        kill_switch: KillSwitch,
        limits: RiskLimits = LIMITS,
        clock: Clock | None = None,
        alert_callback: Callable[[Any], None] | None = None,
        stale_data_threshold_seconds: float = 300.0,
    ):
        self.broker = broker
        self.kill_switch = kill_switch
        self.limits = limits
        self.clock = clock or RealClock()
        self.alert_callback = alert_callback
        self.stale_data_threshold_seconds = stale_data_threshold_seconds
        self._last_heartbeat: datetime = self.clock.now()
        self._last_equity_update: datetime = self.clock.now()

    def record_heartbeat(self) -> None:
        """Called by primary services to signal liveness."""
        self._last_heartbeat = self.clock.now()

    def check_liveness(self, max_silence_seconds: float = 60.0) -> bool:
        """
        Returns False if primary core has been silent too long.
        Fires a heartbeat_alert via alert_callback on failure.
        """
        silence = (self.clock.now() - self._last_heartbeat).total_seconds()
        if silence > max_silence_seconds:
            logger.error(
                "WATCHDOG ALERT: System heartbeat silent.",
                extra={"silence_seconds": silence, "max_silence_seconds": max_silence_seconds},
            )
            if self.alert_callback:
                self.alert_callback(heartbeat_alert(silence, max_silence_seconds))
            return False
        return True

    def run_health_cycle(self) -> bool:
        """
        Executes one monitoring cycle:
        1. Queries current portfolio equity from broker.
        2. Checks whether the equity data is fresh (stale data alert if not).
        3. Evaluates peak-to-trough drawdown via KillSwitch.
        4. If breached, cancels all open broker orders and fires a halt_alert.
        """
        try:
            summary = self.broker.get_account_summary()
            current_equity = summary.get("total_equity", 0.0)

            # ── Stale data check ────────────────────────────────────────────
            now = self.clock.now()
            equity_silence = (now - self._last_equity_update).total_seconds()
            if equity_silence > self.stale_data_threshold_seconds:
                logger.warning(
                    "WATCHDOG: Equity data may be stale.",
                    extra={
                        "silence_seconds": equity_silence,
                        "threshold": self.stale_data_threshold_seconds,
                    },
                )
                if self.alert_callback:
                    self.alert_callback(
                        stale_data_alert("broker_equity", equity_silence, self.stale_data_threshold_seconds)
                    )
            self._last_equity_update = now

            # ── Drawdown / kill switch check ────────────────────────────────
            is_halted, dd = self.kill_switch.check_equity(current_equity)
            if is_halted:
                cancelled_count = self.broker.cancel_all_orders()
                reason = (
                    f"Global HALT triggered at {dd:.2%} drawdown. "
                    f"Cancelled {cancelled_count} working orders. Trading suspended."
                )
                logger.critical(f"WATCHDOG INTERVENTION: {reason}")
                if self.alert_callback:
                    self.alert_callback(halt_alert(reason))
                return False

            return True
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("Watchdog cycle error", extra={"error": str(e)})
            return False
