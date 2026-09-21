from datetime import UTC, datetime, timedelta

from axel.core.clock import RealClock, SimulatedClock


def test_real_clock():
    clock = RealClock()
    now1 = clock.now()
    assert now1.tzinfo is not None
    assert now1.year >= 2026


def test_simulated_clock():
    start = datetime(2026, 3, 15, 10, 0, tzinfo=UTC)
    clock = SimulatedClock(start)
    assert clock.now() == start

    clock.advance(timedelta(minutes=15))
    assert clock.now() == datetime(2026, 3, 15, 10, 15, tzinfo=UTC)

    clock.sleep(30)
    assert clock.now() == datetime(2026, 3, 15, 10, 15, 30, tzinfo=UTC)
