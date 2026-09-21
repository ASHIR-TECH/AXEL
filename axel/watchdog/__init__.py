"""
Watchdog service for system health, heartbeats, and independent circuit breakers.
"""

from axel.watchdog.monitor import WatchdogMonitor

__all__ = ["WatchdogMonitor"]
