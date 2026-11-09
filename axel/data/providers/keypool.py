"""Rotating credential pool for read-only market-data providers.

A single provider account has a rate limit. When several accounts are
available, rotating between them keeps ingestion moving instead of stalling on
the first ``429``. This module is deliberately independent of any concrete
provider and of settings, so the selection policy can be tested with a fake
clock and no network.

Strategies:

* ``failover`` (default) -- keep using the current credential until it is rate
  limited, then move to the next available one.
* ``round_robin`` -- cycle through credentials on every acquisition.
* ``random`` -- pick uniformly from the currently available credentials.

Rate limits are quarantined per credential for a cooldown window (or the
provider's ``Retry-After`` when supplied). Quarantine is process-local and
thread-safe; secrets are never rendered in ``repr`` or snapshots.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

Strategy = Literal["failover", "round_robin", "random"]

_STRATEGIES: frozenset[str] = frozenset({"failover", "round_robin", "random"})


@dataclass(frozen=True)
class Credential:
    """One API key pair. ``key``/``secret`` are excluded from ``repr``."""

    key: str = field(repr=False)
    secret: str = field(repr=False)
    label: str = ""

    def __post_init__(self) -> None:
        if not self.key or not self.secret:
            raise ValueError("credential requires both a key and a secret")

    @property
    def name(self) -> str:
        """Safe display name: label, or the suffix of the key ID."""
        return self.label or f"key-…{self.key[-4:]}"


class AllCredentialsExhausted(RuntimeError):
    """Raised when every credential is inside its rate-limit cooldown."""

    def __init__(self, retry_after_s: float) -> None:
        self.retry_after_s = max(0.0, retry_after_s)
        super().__init__(
            f"all credentials are rate limited; retry in {self.retry_after_s:.1f}s"
        )


class CredentialPool:
    """Thread-safe credential selector with per-key rate-limit quarantine."""

    def __init__(
        self,
        credentials: list[Credential] | tuple[Credential, ...],
        *,
        strategy: Strategy = "failover",
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        if strategy not in _STRATEGIES:
            raise ValueError(f"invalid strategy {strategy!r}; expected one of {sorted(_STRATEGIES)}")
        if not credentials:
            raise ValueError("credential pool requires at least one credential")
        if cooldown_s < 0.0:
            raise ValueError("cooldown_s must be non-negative")

        unique: list[Credential] = []
        seen: set[str] = set()
        for credential in credentials:
            if credential.key in seen:
                continue
            seen.add(credential.key)
            unique.append(credential)

        self._credentials = tuple(unique)
        self._strategy: Strategy = strategy
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._blocked_until: dict[int, float] = {}
        self._current = 0
        self._next = 0
        self._rate_limit_hits = 0

    @property
    def size(self) -> int:
        return len(self._credentials)

    @property
    def rate_limit_hits(self) -> int:
        with self._lock:
            return self._rate_limit_hits

    def acquire(self) -> Credential:
        """Return the next usable credential, or raise if all are quarantined."""
        with self._lock:
            now = self._clock()
            available = [
                index
                for index in range(len(self._credentials))
                if self._blocked_until.get(index, 0.0) <= now
            ]
            if not available:
                wait = min(self._blocked_until.values()) - now
                raise AllCredentialsExhausted(wait)

            if self._strategy == "random":
                index = self._rng.choice(available)
            elif self._strategy == "round_robin":
                index = self._next_available(available)
                self._next = (index + 1) % len(self._credentials)
            else:  # failover
                index = self._current if self._current in available else available[0]
                self._current = index
            return self._credentials[index]

    def _next_available(self, available: list[int]) -> int:
        total = len(self._credentials)
        for step in range(total):
            candidate = (self._next + step) % total
            if candidate in available:
                return candidate
        return available[0]  # pragma: no cover - available is non-empty

    def report_rate_limited(
        self, credential: Credential, retry_after_s: float | None = None
    ) -> None:
        """Quarantine ``credential`` for ``retry_after_s`` (or the cooldown)."""
        index = self._index_of(credential)
        wait = self._cooldown_s if retry_after_s is None else max(0.0, retry_after_s)
        with self._lock:
            self._blocked_until[index] = self._clock() + wait
            self._rate_limit_hits += 1
            if self._current == index:
                self._current = (index + 1) % len(self._credentials)

    def report_success(self, credential: Credential) -> None:
        """Clear any quarantine and make ``credential`` the failover current."""
        index = self._index_of(credential)
        with self._lock:
            self._blocked_until.pop(index, None)
            if self._strategy == "failover":
                self._current = index

    def available_count(self) -> int:
        with self._lock:
            now = self._clock()
            return sum(
                1
                for index in range(len(self._credentials))
                if self._blocked_until.get(index, 0.0) <= now
            )

    def snapshot(self) -> tuple[dict[str, object], ...]:
        """Secret-free view for logs and health endpoints."""
        with self._lock:
            now = self._clock()
            return tuple(
                {
                    "label": credential.name,
                    "blocked_for_s": round(
                        max(0.0, self._blocked_until.get(index, 0.0) - now), 3
                    ),
                }
                for index, credential in enumerate(self._credentials)
            )

    def _index_of(self, credential: Credential) -> int:
        for index, candidate in enumerate(self._credentials):
            if candidate == credential:
                return index
        raise ValueError(f"credential {credential.name} does not belong to this pool")


def pool_from_pairs(
    pairs: list[tuple[str, str]] | tuple[tuple[str, str], ...],
    *,
    strategy: Strategy = "failover",
    cooldown_s: float = 60.0,
    rng: random.Random | None = None,
) -> CredentialPool:
    """Build a pool from ``(key, secret)`` pairs."""
    return CredentialPool(
        [Credential(key, secret) for key, secret in pairs],
        strategy=strategy,
        cooldown_s=cooldown_s,
        rng=rng,
    )


__all__ = [
    "AllCredentialsExhausted",
    "Credential",
    "CredentialPool",
    "Strategy",
    "pool_from_pairs",
]
