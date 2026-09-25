"""Pre-committed deterministic strategy admission gate."""

from dataclasses import dataclass

from axel.data.backtester import BacktestResult


@dataclass(frozen=True)
class ValidationPolicy:
    min_trades: int = 100
    min_expectancy_r: float = 0.001
    max_expectancy_r: float = 0.01
    min_sharpe: float = 0.7
    max_drawdown: float = 0.20


@dataclass(frozen=True)
class ValidationReport:
    approved: bool
    reasons: tuple[str, ...]
    result: BacktestResult


def validate_strategy(
    result: BacktestResult, policy: ValidationPolicy | None = None
) -> ValidationReport:
    policy = policy or ValidationPolicy()
    reasons: list[str] = []
    if result.trades < policy.min_trades:
        reasons.append("INSUFFICIENT_OUT_OF_SAMPLE_TRADES")
    if result.expectancy_r < policy.min_expectancy_r:
        reasons.append("EXPECTANCY_BELOW_THRESHOLD")
    if result.expectancy_r > policy.max_expectancy_r:
        reasons.append("EXPECTANCY_REQUIRES_LEAKAGE_AUDIT")
    if result.sharpe < policy.min_sharpe:
        reasons.append("SHARPE_BELOW_THRESHOLD")
    if result.max_drawdown > policy.max_drawdown:
        reasons.append("MAX_DRAWDOWN_EXCEEDED")
    return ValidationReport(not reasons, tuple(reasons), result)
