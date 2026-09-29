"""
Daily-allocation bankroll for the Telegram betting-signal income stream.

Risk math adapted from the SportyClaw-Autoplacer project
(https://github.com/Contractor-x/SportyClaw-Autoplacer, engine.py + bankroll.py):
the tier-anchored daily allocation cap is generalized here so AXEL can bound
daily staking across the predictions section without coupling to a bookmaker.

Values are currency-agnostic base units.
"""

from dataclasses import dataclass
from itertools import pairwise
from math import isfinite

ACCOUNT_UNITS = 100_000.0  # EUI default in base units

# (balance, max_daily_allocation) tier anchors.
DEFAULT_ANCHORS: tuple[tuple[int, int], ...] = (
    (5_000, 2_000),
    (50_000, 20_000),
    (500_000, 100_000),
    (1_000_000, 200_000),
)


@dataclass(frozen=True)
class AllocationResult:
    balance: float
    allocation: float
    reserve: float


def compute_daily_allocation(
    balance: float, anchors: tuple[tuple[int, int], ...] = DEFAULT_ANCHORS
) -> AllocationResult:
    if balance <= 0 or not isfinite(balance):
        return AllocationResult(balance=0.0, allocation=0.0, reserve=0.0)

    sorted_anchors = sorted(anchors, key=lambda x: x[0])

    def _interpolate(x: float) -> float:
        if x <= sorted_anchors[0][0]:
            x1, y1 = sorted_anchors[0]
            return (x / x1) * y1
        for (x1, y1), (x2, y2) in pairwise(sorted_anchors):
            if x1 <= x <= x2:
                slope = (y2 - y1) / (x2 - x1)
                return y1 + slope * (x - x1)
        (x1, y1), (x2, y2) = sorted_anchors[-2], sorted_anchors[-1]
        slope = (y2 - y1) / (x2 - x1)
        return y2 + slope * (x - x2)

    allocation = min(max(_interpolate(balance), 0.0), balance)
    return AllocationResult(
        balance=round(balance, 2),
        allocation=round(allocation, 2),
        reserve=round(max(balance - allocation, 0.0), 2),
    )


@dataclass
class AllocationBankroll:
    """Tracks how much of today's allocation may still be staked.

    Every stake is reserved before placement and released on failure, mirroring
    the SportyClaw listener flow. This is the ONLY gate between an incoming
    bet signal and money leaving the account.
    """

    balance: float = 0.0
    allocation_total: float = 0.0
    allocation_remaining: float = 0.0
    max_bets_per_day: int = 30
    bets_remaining: int = 0
    anchors: tuple[tuple[int, int], ...] = DEFAULT_ANCHORS

    def initialize(self, amount: float, max_bets_per_day: int = 30) -> None:
        amount = max(0.0, float(amount))
        allocation = compute_daily_allocation(amount, self.anchors).allocation
        self.balance = round(amount, 2)
        self.allocation_total = round(allocation, 2)
        self.allocation_remaining = self.allocation_total
        self.max_bets_per_day = max(0, int(max_bets_per_day))
        self.bets_remaining = self.max_bets_per_day if allocation > 0 else 0

    def reserve_stake(self) -> float | None:
        if self.allocation_remaining <= 0 or self.bets_remaining <= 0:
            return None
        kobo_of = lambda value: max(0, round(value * 100))
        remaining_kobo = kobo_of(self.allocation_remaining)
        if remaining_kobo <= 0:
            return None
        stake_kobo = remaining_kobo if self.bets_remaining == 1 else remaining_kobo // self.bets_remaining
        if stake_kobo <= 0:
            return None
        stake = round(stake_kobo / 100, 2)
        self.allocation_remaining = round(self.allocation_remaining - stake, 2)
        self.bets_remaining = max(0, self.bets_remaining - 1)
        return stake

    def release_stake(self, amount: float) -> None:
        amount = max(0.0, float(amount))
        if amount <= 0:
            return
        self.allocation_remaining = round(
            min(self.allocation_remaining + amount, self.allocation_total), 2
        )
        self.bets_remaining = min(self.max_bets_per_day, self.bets_remaining + 1)

    def has_available_allocation(self) -> bool:
        return self.allocation_remaining > 0 and self.bets_remaining > 0

    def remaining(self) -> float:
        return self.allocation_remaining

    def state(self) -> dict:
        return {
            "balance": self.balance,
            "allocation_total": self.allocation_total,
            "allocation_remaining": self.allocation_remaining,
            "bets_remaining": self.bets_remaining,
            "max_bets_per_day": self.max_bets_per_day,
        }