"""Validated strategies.

Nothing lives here yet: a strategy is promoted into this package only after it
clears every admission gate in :mod:`axel.validation.admission` (trade count,
expectancy band, net Sharpe, drawdown, regime count, deflated Sharpe). Phase 2
cannot promote a strategy directly here.
"""

from __future__ import annotations

__all__: list[str] = []