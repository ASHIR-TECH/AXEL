"""Section selection for the paper runtime.

The synthetic feed covers every section (stocks, options, crypto, forex,
predictions). The real Alpaca paper feed only covers stocks, so selecting it
narrows the runtime rather than letting proposals form on stale synthetic bars.
"""

from __future__ import annotations

from axel.core.config import settings
from axel.runtime.paper_feed import SECTION_SYMBOLS


def trading_sections() -> dict[str, tuple[str, ...]]:
    """Return the sections the runtime is allowed to trade in this mode."""
    if settings.paper_data_source == "alpaca":
        return {"STOCKS": SECTION_SYMBOLS["STOCKS"]}
    return {section: symbols for section, symbols in SECTION_SYMBOLS.items()}


__all__ = ["trading_sections"]
