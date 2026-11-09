"""Tests for the rotating credential pool."""

from __future__ import annotations

import random

import pytest

from axel.data.providers.keypool import (
    AllCredentialsExhausted,
    Credential,
    CredentialPool,
    pool_from_pairs,
)


def _clock(start: float = 0.0):
    now = [start]
    return now, lambda: now[0]


def _pool(strategy="failover", *, cooldown=60.0, count=2):
    now, clock = _clock()
    credentials = [Credential(f"key-{index}", f"secret-{index}") for index in range(count)]
    pool = CredentialPool(credentials, strategy=strategy, cooldown_s=cooldown, clock=clock)
    return pool, credentials, now


def test_failover_sticks_then_switches_on_rate_limit() -> None:
    pool, credentials, now = _pool()
    assert pool.acquire() is credentials[0]
    assert pool.acquire() is credentials[0]

    pool.report_rate_limited(credentials[0])
    assert pool.acquire() is credentials[1]

    now[0] = 61.0
    assert pool.available_count() == 2


def test_round_robin_cycles_every_call() -> None:
    pool, credentials, _now = _pool("round_robin")
    assert pool.acquire() is credentials[0]
    assert pool.acquire() is credentials[1]
    assert pool.acquire() is credentials[0]


def test_random_uses_seeded_rng_and_available_set() -> None:
    _now, clock = _clock()
    credentials = [Credential(f"key-{index}", f"secret-{index}") for index in range(3)]
    first = CredentialPool(credentials, strategy="random", clock=clock, rng=random.Random(7))
    second = CredentialPool(credentials, strategy="random", clock=clock, rng=random.Random(7))
    sequence_a = [first.acquire().key for _ in range(6)]
    sequence_b = [second.acquire().key for _ in range(6)]
    assert sequence_a == sequence_b

    first.report_rate_limited(credentials[0], retry_after_s=10)
    for _ in range(6):
        assert first.acquire() is not credentials[0]


def test_all_exhausted_reports_earliest_reset() -> None:
    pool, credentials, _now = _pool(cooldown=10)
    pool.report_rate_limited(credentials[0], retry_after_s=5)
    pool.report_rate_limited(credentials[1], retry_after_s=3)

    with pytest.raises(AllCredentialsExhausted) as excinfo:
        pool.acquire()
    assert excinfo.value.retry_after_s == pytest.approx(3.0)


def test_success_clears_quarantine_and_resets_failover() -> None:
    pool, credentials, _now = _pool()
    pool.report_rate_limited(credentials[0])
    assert pool.acquire() is credentials[1]

    pool.report_success(credentials[0])
    assert pool.acquire() is credentials[0]


def test_snapshot_and_repr_never_expose_secrets() -> None:
    pool, credentials, _now = _pool()
    snapshot = pool.snapshot()
    assert snapshot[0]["label"] == credentials[0].name
    assert "secret" not in repr(snapshot)
    assert "secret-0" not in repr(credentials[0])
    assert "secret-0" not in repr(pool)


def test_pool_deduplicates_by_key_id() -> None:
    pool = pool_from_pairs([("same", "one"), ("same", "two"), ("other", "three")])
    assert pool.size == 2


def test_pool_rejects_empty_credentials_and_bad_strategy() -> None:
    with pytest.raises(ValueError):
        CredentialPool([])
    with pytest.raises(ValueError):
        CredentialPool([Credential("key", "secret")], strategy="bogus")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Credential("", "secret")
    with pytest.raises(ValueError):
        Credential("key", "")
