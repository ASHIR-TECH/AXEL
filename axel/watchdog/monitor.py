"""
Independent Watchdog process monitor.
Periodically checks portfolio equity drawdown, heartbeat freshness, and fires HALT when breached.
"""

from collections.abc import Callable
from datetime import datetime

from axel.core.clock import Clock, RealClock
from axel.core.logging import logger
from axel.execution.broker_base import BrokerAdapter
from axel.risk.killswitch import KillSwitch
from axel.risk.limits import LIMITS, RiskLimits


class WatchdogMonitor:
    """
    Independent watchdog process runner.
    Monitors high-water mark drawdown and sets global HALT if drawdown exceeds 10%.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        kill_switch: KillSwitch,
        limits: RiskLimits = LIMITS,
        clock: Clock | None = None,
        alert_callback: Callable[[str], None] | None = None,
    ):
        self.broker = broker
        self.kill_switch = kill_switch
        self.limits = limits
        self.clock = clock or RealClock()
        self.alert_callback = alert_callback
        self._last_heartbeat: datetime = self.clock.now()

    def record_heartbeat(self) -> None:
        """Called by primary services to signal liveness."""
        self._last_heartbeat = self.clock.now()

    def check_liveness(self, max_silence_seconds: float = 60.0) -> bool:
        """Returns False if primary core has been silent too long."""
        silence = (self.clock.now() - self._last_heartbeat).total_seconds()
        if silence > max_silence_seconds:
            msg = f"WATCHDOG ALERT: System heartbeat silent for {silence:.1f}s (max {max_silence_seconds}s)!"
            logger.error(msg)
            if self.alert_callback:
                self.alert_callback(msg)
            return False
        return True

    def run_health_cycle(self) -> bool:
        """
        Executes one monitoring cycle:
        1. Queries current portfolio equity from broker.
        2. Evaluates peak-to-trough drawdown via KillSwitch.
        3. If breached, cancels all open broker orders and trips HALT.
        """
        try:
            summary = self.broker.get_account_summary()
            current_equity = summary.get("total_equity", 0.0)

            is_halted, dd = self.kill_switch.check_equity(current_equity)
            if is_halted:
                # Emergency intervention: Cancel all working orders
                cancelled_count = self.broker.cancel_all_orders()
                msg = (
                    f"WATCHDOG INTERVENTION: Global HALT triggered at {dd:.2%} drawdown. "
                    f"Cancelled {cancelled_count} working orders. Trading suspended."
                )
                logger.critical(msg)
                if self.alert_callback:
                    self.alert_callback(msg)
                return False

            return True
        except (OSError, RuntimeError, ValueError) as e:
            logger.error("Watchdog cycle error", extra={"error": str(e)})
            return False
