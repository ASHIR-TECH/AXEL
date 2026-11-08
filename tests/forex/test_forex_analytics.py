"""Unit tests for Phase 7b forex carry, exposure and macro-event helpers."""

from datetime import UTC, datetime, timedelta

import pytest

from axel.forex.analytics import (
    MacroEvent,
    PositionExposure,
    broker_carry,
    carry_return,
    currency_exposure,
    in_macro_blackout,
    net_carry,
    upcoming_events,
    visible_events,
)


def _event(
    *,
    event_time: datetime,
    published_at: datetime,
    currency: str = "USD",
    category: str = "CPI",
    importance: int = 3,
    actual: float | None = 3.2,
) -> MacroEvent:
    return MacroEvent(
        event_time=event_time,
        currency=currency,
        category=category,
        previous=3.0,
        forecast=3.1,
        actual=actual,
        published_at=published_at,
        source="test",
        importance=importance,
    )


def test_carry_return_long_base_short_quote():
    # Long USD/JPY: 5% USD, 0.1% JPY, one year, plus 2% FX return.
    value = carry_return(0.05, 0.001, 1.0, 0.02, direction=1)
    assert value == pytest.approx(0.069)


def test_carry_return_short_is_opposite_sign():
    long_value = carry_return(0.05, 0.001, 1.0, 0.02, direction=1)
    short_value = carry_return(0.05, 0.001, 1.0, 0.02, direction=-1)
    assert short_value == pytest.approx(-long_value)


def test_carry_return_scales_with_holding_period():
    half_year = carry_return(0.04, 0.01, 0.5)
    assert half_year == pytest.approx(0.015)


def test_carry_return_rejects_invalid_direction_and_period():
    with pytest.raises(ValueError):
        carry_return(0.05, 0.01, 1.0, direction=0)
    with pytest.raises(ValueError):
        carry_return(0.05, 0.01, -1.0)


def test_broker_carry_uses_actual_rollover_and_direction():
    assert broker_carry(rollover_per_unit=-0.25, units=1000.0, direction=1) == -250.0
    assert broker_carry(rollover_per_unit=-0.25, units=1000.0, direction=-1) == 250.0


def test_net_carry_prefers_broker_rollover_over_model():
    # The policy-rate model is ignored when actual rollover data exists.
    assert net_carry(100.0, broker_rollover=80.0) == 80.0
    assert net_carry(100.0) == 100.0
    assert net_carry(100.0, broker_rollover=80.0, financing_cost=5.0, spread_cost=2.0) == 73.0


def test_net_carry_rejects_negative_costs():
    with pytest.raises(ValueError):
        net_carry(10.0, financing_cost=-1.0)
    with pytest.raises(ValueError):
        net_carry(10.0, spread_cost=-1.0)


def test_currency_exposure_aggregates_all_instruments():
    positions = [
        PositionExposure("spy", value=10_000.0, exposures={"USD": 1.0}),
        PositionExposure("eurusd", value=5_000.0, exposures={"EUR": 1.0, "USD": -1.0}),
        PositionExposure("adr", value=2_000.0, exposures={"USD": 0.5, "GBP": 0.5}),
    ]
    exposure = currency_exposure(positions)
    assert exposure == {
        "EUR": 5_000.0,
        "GBP": 1_000.0,
        "USD": 10_000.0 - 5_000.0 + 1_000.0,
    }


def test_currency_exposure_reconciles_to_position_values():
    positions = [
        PositionExposure("a", value=100.0, exposures={"USD": 0.6, "EUR": 0.4}),
        PositionExposure("b", value=-50.0, exposures={"USD": 1.0}),
    ]
    exposure = currency_exposure(positions)
    assert sum(exposure.values()) == pytest.approx(
        sum(position.value for position in positions)
    )


def test_currency_exposure_is_sorted_and_rejects_non_finite():
    exposure = currency_exposure(
        [PositionExposure("a", value=1.0, exposures={"USD": 1.0, "AUD": 1.0})]
    )
    assert list(exposure) == ["AUD", "USD"]
    with pytest.raises(ValueError):
        PositionExposure("bad", value=float("nan"), exposures={"USD": 1.0})


def test_visible_events_respects_publication_time():
    event_time = datetime(2026, 1, 5, 13, 30, tzinfo=UTC)
    published_late = event_time + timedelta(minutes=5)
    event = _event(event_time=event_time, published_at=published_late)

    assert visible_events([event], event_time) == ()
    assert visible_events([event], published_late) == (event,)


def test_visible_events_preserves_timestamps_and_sorts():
    base = datetime(2026, 1, 5, 13, 30, tzinfo=UTC)
    later = _event(
        event_time=base + timedelta(hours=1),
        published_at=base,
        category="NFP",
    )
    earlier = _event(event_time=base, published_at=base, category="CPI")
    result = visible_events([later, earlier], base + timedelta(hours=2))
    assert result == (earlier, later)
    assert result[0].event_time == base
    assert result[0].published_at == base


def test_visible_events_rejects_naive_decision_time():
    with pytest.raises(ValueError):
        visible_events([], datetime(2026, 1, 5, 13, 30))


def test_upcoming_events_filters_horizon_currency_and_importance():
    base = datetime(2026, 1, 5, 13, 30, tzinfo=UTC)
    usd = _event(event_time=base + timedelta(hours=1), published_at=base, currency="USD")
    eur_low = _event(
        event_time=base + timedelta(hours=2),
        published_at=base,
        currency="EUR",
        importance=1,
    )
    far = _event(event_time=base + timedelta(days=2), published_at=base, currency="USD")

    result = upcoming_events(
        [usd, eur_low, far],
        base,
        timedelta(hours=3),
        currency="USD",
        min_importance=2,
    )
    assert result == (usd,)


def test_macro_blackout_window():
    base = datetime(2026, 1, 5, 13, 30, tzinfo=UTC)
    event = _event(event_time=base, published_at=base, currency="USD")

    assert in_macro_blackout(
        [event],
        base - timedelta(minutes=5),
        currency="USD",
        before=timedelta(minutes=15),
        after=timedelta(minutes=15),
    )
    assert not in_macro_blackout(
        [event],
        base - timedelta(minutes=30),
        currency="USD",
        before=timedelta(minutes=15),
        after=timedelta(minutes=15),
    )
    assert not in_macro_blackout(
        [event],
        base,
        currency="EUR",
        before=timedelta(minutes=15),
        after=timedelta(minutes=15),
    )


def test_macro_event_rejects_naive_timestamps():
    base = datetime(2026, 1, 5, 13, 30)
    with pytest.raises(ValueError):
        _event(event_time=base, published_at=base)
