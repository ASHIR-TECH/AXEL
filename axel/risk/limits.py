"""
Immutable hardcoded risk limits for AXEL.
These limits CANNOT be altered or overridden by any LLM output or runtime prompt.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    """
    Core deterministic risk thresholds.
    Enforced strictly across all sections and strategies.
    """
    GLOBAL_MAX_DRAWDOWN: float = 0.10          # 10% peak-to-trough account halt (kill switch)
    SECTION_DAILY_STOP: float = 0.03           # 3% section daily stop-loss
    MAX_POSITION_PCT: float = 0.05             # 5% max notional exposure per trade
    KELLY_CAP: float = 0.25                    # Fractional Kelly scaling factor (0.25 * f*)
    MAX_PER_TRADE_RISK_PCT: float = 0.01       # 1% max capital at risk at stop distance
    MIN_RR_RATIO: float = 1.5                  # 1.5:1 minimum reward-to-risk ratio
    MAX_EXPECTED_R: float = 4.0                # >4.0R flagged as overfitting / leakage
    MIN_PANEL_CONFIDENCE: float = 0.30         # Proposals below 0.30 confidence auto-rejected (HOLD)
    MAX_OPEN_POSITIONS_PER_SECTION: int = 10   # Max concurrent active trades per section
    CORRELATION_BLOCK_THRESHOLD: float = 0.85  # Reject if correlation with active trade > 0.85
    MAX_REALLOCATION_PCT_PER_CYCLE: float = 0.05  # Max 5% capital shift across sections per cycle


LIMITS = RiskLimits()
