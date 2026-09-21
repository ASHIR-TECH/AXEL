"""
Pytest configuration, fixtures, and database lifecycle.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from axel.core.clock import SimulatedClock
from axel.db.base import Base


@pytest.fixture
def sim_clock():
    """Provides a deterministic simulated clock."""
    return SimulatedClock(datetime(2026, 6, 1, 9, 30, tzinfo=UTC))


@pytest.fixture
def in_memory_db():
    """Creates a fresh in-memory SQLite database for test isolation."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
