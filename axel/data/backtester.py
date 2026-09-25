"""Small deterministic, cost-aware baseline backtester for validated rule strategies."""

from dataclasses import dataclass
from math import sqrt

from axel.data.models import Bar


@dataclass(frozen=True)
class BacktestResult:
    trades: int
    net_returns: tuple[float, ...]
    expectancy_r: float
    sharpe: float
    max_drawdown: float


def trend_returns(bars: list[Bar], *, lookback: int = 3, fee_bps: float = 1.0, slippage_bps: float = 1.0) -> BacktestResult:
    """Long-only trend baseline; signals use prior closes only, preventing look-ahead."""
    if lookback < 2 or len(bars) <= lookback:
        raise ValueError("insufficient bars for selected lookback")
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    returns: list[float] = []
    cost = (fee_bps + slippage_bps) / 10_000
    for index in range(lookback, len(ordered) - 1):
        history = ordered[index - lookback:index]
        if history[-1].close <= history[0].close:
            continue
        entry, exit_ = ordered[index].close, ordered[index + 1].close
        returns.append((exit_ - entry) / entry - cost)
    return summarize_returns(returns)


def summarize_returns(returns: list[float]) -> BacktestResult:
    if not returns:
        return BacktestResult(0, (), 0.0, 0.0, 0.0)
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    sharpe = mean / sqrt(variance) * sqrt(252) if variance else 0.0
    equity, peak, max_drawdown = 1.0, 1.0, 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return BacktestResult(len(returns), tuple(returns), mean, sharpe, max_drawdown)
