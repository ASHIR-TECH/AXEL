"""Unit and quantitative tests for Point-in-Time integrity and dataset lineage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from axel.research.dataset import (
    DatasetLineage,
    DatasetRegistry,
    DatasetVersion,
    PointInTimeChecker,
    ensure_utc,
)


def test_point_in_time_availability_rules():
    t0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    t_pub = t0 + timedelta(hours=2)
    t_ing = t0 + timedelta(hours=4)

    ds = DatasetVersion(
        dataset_id="sec_filings_10k",
        version="v1",
        event_time=t0,
        publication_time=t_pub,
        ingestion_time=t_ing,
    )

    # 1. Before event: NOT available
    assert not ds.is_available_at(t0 - timedelta(minutes=5))

    # 2. After event, but before publication: NOT available
    assert not ds.is_available_at(t0 + timedelta(hours=1))

    # 3. After publication, but before system ingestion: NOT available
    assert not ds.is_available_at(t0 + timedelta(hours=3))

    # 4. After ingestion: AVAILABLE
    assert ds.is_available_at(t0 + timedelta(hours=5))


def test_timezone_safety_naive_and_aware():
    # Naive timestamp should be automatically normalized without TypeError
    naive_decision = datetime(2026, 5, 1, 10, 0)
    aware_pub = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
    aware_ing = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)

    ds = DatasetVersion(
        dataset_id="quotes",
        version="v1",
        publication_time=aware_pub,
        ingestion_time=aware_ing,
    )
    assert ds.is_available_at(naive_decision) is True


def test_content_hash_verification():
    raw_bytes = b"timestamp,symbol,close\n2026-01-01,AAPL,150.0\n"
    expected_hash = sha256(raw_bytes).hexdigest()

    ds = DatasetVersion(
        dataset_id="aapl_daily",
        version="2026_01",
        content_hash=expected_hash,
    )
    assert ds.verify_content_hash(raw_bytes) is True
    assert ds.verify_content_hash(b"corrupted data") is False


def test_point_in_time_checker_and_observation_leakage():
    registry = DatasetRegistry()
    t_avail = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)

    ds = DatasetVersion(
        dataset_id="prices",
        version="v1",
        ingestion_time=t_avail,
    )
    registry.register(ds)

    checker = PointInTimeChecker(registry)

    # Dataset level check
    ok, errors = checker.validate([("prices", "v1")], datetime(2026, 2, 1, tzinfo=UTC))
    assert not ok
    assert len(errors) == 1

    ok, errors = checker.validate([("prices", "v1")], datetime(2026, 3, 2, tzinfo=UTC))
    assert ok
    assert len(errors) == 0

    # Observation level sequence leakage check
    observations = [
        {"timestamp": datetime(2026, 2, 28, 16, 0, tzinfo=UTC), "close": 100.0},
        {"timestamp": datetime(2026, 3, 1, 16, 0, tzinfo=UTC), "close": 102.0},
        {"timestamp": datetime(2026, 3, 2, 16, 0, tzinfo=UTC), "close": 105.0},
    ]

    # At decision timestamp 2026-03-01 18:00, the last observation (3-2) is from the future
    decision_ts = datetime(2026, 3, 1, 18, 0, tzinfo=UTC)
    leak_ok, leak_errs = checker.validate_observations(observations, decision_ts)
    assert not leak_ok
    assert any("look-ahead leakage" in err for err in leak_errs)
