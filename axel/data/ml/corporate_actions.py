"""
Corporate actions and point-in-time universe membership.

Two failure modes are addressed here:

*Corporate actions.* A price-only backtest silently misprices dividend payers
(low-yield names look like losers) and cannot represent a delisting at all.
Splits were already handled; this adds cash dividends and terminal delisting.

*Survivorship bias.* Building a universe from "symbols with data today" quietly
removes every company that went bankrupt, which flatters every backtest.
``PointInTimeUniverse`` resolves membership as of a date from listing records,
so a name that delisted in 2019 is present in 2018 universes and absent from 2024
ones -- the bias is prevented structurally rather than by remembering to filter.

Reference data (dividends, delistings, listings) is only ever applied as of the
date it became effective, never retroactively to dates before it existed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum

from axel.data.ml.splits import Split, apply_splits
from axel.data.schemas import BarRecord


class TerminalPricePolicy(str, Enum):
    """How a delisted holding is marked on its final event."""

    MARK_TO_ZERO = "mark_to_zero"
    """Delisting risk is real: assume recovery is zero (default, conservative)."""

    LAST_TRADE = "last_trade"
    """Assume exit at the last observed close, i.e. the delisting is not a loss."""


@dataclass(frozen=True)
class Dividend:
    """A cash dividend, keyed on its ex-date (the first date without the right)."""

    symbol: str
    ex_date: date
    amount: float
    pay_date: date | None = None
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("dividend requires a symbol")
        if self.amount < 0:
            raise ValueError("dividend amount cannot be negative")
        if self.pay_date is not None and self.pay_date < self.ex_date:
            raise ValueError("dividend pay_date cannot precede ex_date")


@dataclass(frozen=True)
class Delisting:
    """Removal of a symbol from an exchange."""

    symbol: str
    delisted_on: date
    reason: str = "unspecified"
    terminal_price_policy: TerminalPricePolicy = TerminalPricePolicy.MARK_TO_ZERO

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("delisting requires a symbol")

    def terminal_price(self, last_price: float | None) -> float:
        if self.terminal_price_policy is TerminalPricePolicy.LAST_TRADE:
            return last_price or 0.0
        return 0.0


@dataclass(frozen=True)
class ListingRecord:
    """One instrument's listing history. ``delisted_on=None`` means still listed."""

    symbol: str
    listed_on: date
    delisted_on: date | None = None
    venue: str = "us-equity"
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("listing requires a symbol")
        if self.delisted_on is not None and self.delisted_on < self.listed_on:
            raise ValueError("delisted_on cannot precede listed_on")

    def is_listed_on(self, day: date) -> bool:
        return self.listed_on <= day and (
            self.delisted_on is None or day < self.delisted_on
        )

    def is_delisted_by(self, day: date) -> bool:
        return self.delisted_on is not None and day >= self.delisted_on


class PointInTimeUniverse:
    """
    As-of-date universe resolution over listing records.

    ``members(as_of)`` is the set of symbols a strategy could legitimately have
    traded on that date. ``survivors_only(as_of)`` reproduces the biased view
    for comparison; ``survivorship_bias`` reports how many members the biased
    view is missing, which is the number to publish alongside any result.
    """

    def __init__(self, listings: Iterable[ListingRecord] = ()) -> None:
        by_symbol: dict[str, ListingRecord] = {}
        for record in listings:
            existing = by_symbol.get(record.symbol)
            if existing is None or record.listed_on < existing.listed_on:
                by_symbol[record.symbol] = record
        self._by_symbol = dict(sorted(by_symbol.items()))

    def __len__(self) -> int:
        return len(self._by_symbol)

    def __contains__(self, symbol: object) -> bool:
        return symbol in self._by_symbol

    @property
    def listings(self) -> tuple[ListingRecord, ...]:
        return tuple(self._by_symbol.values())

    def record(self, symbol: str) -> ListingRecord | None:
        return self._by_symbol.get(symbol)

    def members(self, as_of: date) -> tuple[str, ...]:
        """Symbols listed and not yet delisted on ``as_of``."""
        return tuple(
            symbol
            for symbol, record in self._by_symbol.items()
            if record.is_listed_on(as_of)
        )

    def survivors_only(self, as_of: date) -> tuple[str, ...]:
        """The biased view: only symbols whose listing is still open today."""
        return tuple(
            symbol
            for symbol, record in self._by_symbol.items()
            if record.is_active and record.is_listed_on(as_of)
        )

    def delisted_by(self, as_of: date) -> tuple[str, ...]:
        return tuple(
            symbol
            for symbol, record in self._by_symbol.items()
            if record.is_delisted_by(as_of)
        )

    def survivorship_bias(self, as_of: date) -> tuple[str, ...]:
        """Members the survivors-only view silently drops (should be empty)."""
        survivors = set(self.survivors_only(as_of))
        return tuple(symbol for symbol in self.members(as_of) if symbol not in survivors)

    def is_tradable(self, symbol: str, as_of: date) -> bool:
        record = self._by_symbol.get(symbol)
        return record is not None and record.is_listed_on(as_of)

    def membership_history(self, symbol: str) -> tuple[date, ...]:
        """Dates on which membership for ``symbol`` changed."""
        record = self._by_symbol.get(symbol)
        if record is None:
            return ()
        return tuple(day for day in (record.listed_on, record.delisted_on) if day)


def dividends_by_symbol(
    dividends: Iterable[Dividend],
) -> dict[str, tuple[Dividend, ...]]:
    grouped: dict[str, list[Dividend]] = {}
    for dividend in dividends:
        grouped.setdefault(dividend.symbol, []).append(dividend)
    return {
        symbol: tuple(sorted(items, key=lambda item: item.ex_date))
        for symbol, items in sorted(grouped.items())
    }


def delistings_by_symbol(
    delistings: Iterable[Delisting],
) -> dict[str, tuple[Delisting, ...]]:
    grouped: dict[str, list[Delisting]] = {}
    for event in delistings:
        grouped.setdefault(event.symbol, []).append(event)
    return {
        symbol: tuple(sorted(items, key=lambda item: item.delisted_on))
        for symbol, items in sorted(grouped.items())
    }


def dividends_in_window(
    dividends: Sequence[Dividend], start: date, end: date
) -> tuple[Dividend, ...]:
    """Dividends with ``start < ex_date <= end``, i.e. earned inside the window."""
    return tuple(dividend for dividend in dividends if start < dividend.ex_date <= end)


def dividend_cash(quantity: float, amount: float) -> float:
    """Cash credited to a holder of ``quantity`` shares on the ex-date."""
    return quantity * amount


def _merged_status(current: str) -> str:
    """Keep the adjustment provenance honest when splits and dividends compose."""
    if current == "raw":
        return "dividend_adjusted"
    if current == "split_adjusted":
        return "split_and_dividend_adjusted"
    return current


def apply_dividends(
    bars: Sequence[BarRecord], dividends: Sequence[Dividend]
) -> list[BarRecord]:
    """
    Backward-adjust prices for dividends to produce a total-return series.

    Each pre-ex-date bar is scaled by ``(1 - amount / previous_close)`` for every
    dividend that has not yet gone ex, so holding through the ex-date is
    return-neutral in the adjusted series (the dividend shows up as return rather
    than as an unexplained gap).

    Idempotent: applied dividends are recorded in provenance flags, so running
    this on its own output is a no-op -- the same contract as ``apply_splits``.
    """
    from dataclasses import replace

    adjusted = list(bars)
    for dividend in sorted(dividends, key=lambda item: item.ex_date):
        relevant = [
            bar
            for bar in adjusted
            if bar.symbol == dividend.symbol and bar.event_time.date() < dividend.ex_date
        ]
        if not relevant:
            continue
        flag = f"dividend:{dividend.ex_date.isoformat()}"
        targets = [
            bar
            for bar in relevant
            if flag not in bar.provenance.quality_flags
        ]
        if not targets:
            continue
        previous = max(
            (bar for bar in adjusted if bar.symbol == dividend.symbol
             and bar.event_time.date() < dividend.ex_date),
            key=lambda bar: bar.event_time,
        )
        if previous.close <= 0 or dividend.amount >= previous.close:
            raise ValueError(
                f"dividend {dividend.amount} on {dividend.symbol} is not payable "
                f"against a close of {previous.close}"
            )
        factor = 1.0 - dividend.amount / previous.close
        for bar in targets:
            adjusted[adjusted.index(bar)] = replace(
                bar,
                open=bar.open * factor,
                high=bar.high * factor,
                low=bar.low * factor,
                close=bar.close * factor,
                adjustment_status=_merged_status(bar.adjustment_status),
                provenance=bar.provenance.with_flags(flag),
            )
    return sorted(adjusted, key=lambda bar: bar.event_time)


def apply_corporate_actions(
    bars: Sequence[BarRecord],
    *,
    splits: Sequence[Split] = (),
    dividends: Sequence[Dividend] = (),
) -> list[BarRecord]:
    """Splits first (prices divide), then dividends (total-return adjustment)."""
    return apply_dividends(apply_splits(bars, splits), dividends)


__all__ = [
    "Delisting",
    "Dividend",
    "ListingRecord",
    "PointInTimeUniverse",
    "TerminalPricePolicy",
    "apply_corporate_actions",
    "apply_dividends",
    "delistings_by_symbol",
    "dividend_cash",
    "dividends_by_symbol",
    "dividends_in_window",
]