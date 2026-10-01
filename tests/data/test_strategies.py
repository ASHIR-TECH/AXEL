from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from axel.data.ml import NO_SLIPPAGE, US_EQUITY_CALENDAR, Backtester, CostModel
from axel.data.schemas import BarRecord, Provenance
from axel.strategies import MeanReversion, StrategyRegistry, StrategyStatus, TrendFollowing
from axel.validation import AdmissionCriteria, evaluate_admission, walk_forward_folds

START = datetime(2026, 1, 5, tzinfo=UTC)  # first 2026 session


def _bars(
    *,
    count: int,
    close_at: Callable[[int], float],
    symbol: str = "AAPL",
) -> list[BarRecord]:
    """Session-dated bars, as a real US equity feed produces."""
    bars = []
    event = START
    for index in range(count):
        while not US_EQUITY_CALENDAR.is_trading_day(event.date()):
            event += timedelta(days=1)
        close = close_at(index)
        bars.append(
            BarRecord(
                symbol=symbol,
                venue="alpaca-iex",
                event_time=event,
                available_at=event,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=10_000.0,
                adjustment_status="raw",
                provenance=Provenance(
                    source="test",
                    source_id=f"{symbol}-{index}",
                    source_hash=f"{symbol}-{index}",
                    ingestion_timestamp=START,
                ),
            )
        )
        event += timedelta(days=1)
    return bars


def _rising(index: int) -> float:
    return 100.0 * (1.004**index)


def _falling(index: int) -> float:
    return 100.0 * (1.004) ** (-index)


def test_metadata_fingerprint_tracks_params_and_version() -> None:
    first = TrendFollowing(short_window=10, long_window=30).metadata()
    same = TrendFollowing(short_window=10, long_window=30).metadata()
    other = TrendFollowing(short_window=12, long_window=30).metadata()
    assert first.fingerprint() == same.fingerprint()
    assert first.fingerprint() != other.fingerprint()
    assert first.status is StrategyStatus.DRAFT


def test_registry_rejects_duplicate_and_illegal_transitions() -> None:
    registry = StrategyRegistry()
    registry.register(TrendFollowing().metadata())
    with pytest.raises(ValueError):
        registry.register(TrendFollowing().metadata())

    with pytest.raises(ValueError):
        registry.transition("baseline.trend", StrategyStatus.PAPER_ELIGIBLE)

    for status in (
        StrategyStatus.BACKTESTED,
        StrategyStatus.VALIDATED,
        StrategyStatus.PAPER_ELIGIBLE,
    ):
        registry.transition("baseline.trend", status)
    assert registry.get("baseline.trend").status is StrategyStatus.PAPER_ELIGIBLE
    assert registry.can_place_live_orders("baseline.trend") is False
    assert len(registry) == 1


def test_trend_following_is_long_in_uptrend_and_flat_in_downtrend() -> None:
    strategy = TrendFollowing(short_window=5, long_window=10)
    up = strategy.decide(START + timedelta(days=40), {"AAPL": tuple(_bars(count=40, close_at=_rising))})
    assert up["AAPL"] == 1.0
    down = strategy.decide(
        START + timedelta(days=40), {"AAPL": tuple(_bars(count=40, close_at=_falling))}
    )
    assert down["AAPL"] == 0.0


def test_trend_following_stays_flat_until_long_window_is_warm() -> None:
    strategy = TrendFollowing(short_window=5, long_window=20)
    warmup = strategy.decide(START + timedelta(days=5), {"AAPL": tuple(_bars(count=5, close_at=_rising))})
    assert warmup["AAPL"] == 0.0


def test_mean_reversion_is_contrarian_and_scales_with_extremity() -> None:
    strategy = MeanReversion(window=10, entry_z=1.0, deadband_z=0.1)
    spike = list(_bars(count=10, close_at=_rising))
    spike[-1] = replace(spike[-1], open=200.0, high=200.0, low=200.0, close=200.0)
    target = strategy.decide(START + timedelta(days=9), {"AAPL": tuple(spike)})["AAPL"]
    assert target == pytest.approx(-1.0)


def test_end_to_end_run_is_measured_and_not_promoted() -> None:
    bars = _bars(count=120, close_at=_rising)
    strategy = TrendFollowing(short_window=10, long_window=30)
    registry = StrategyRegistry()
    registry.register(strategy.metadata())

    result = Backtester(
        slippage=NO_SLIPPAGE,
        costs=CostModel(commission_bps=5),
        risk_per_trade=0.01,
    ).run({"AAPL": bars}, strategy)
    metrics = result.metrics()

    assert len(result.equity_curve) == 120
    assert metrics["total_return"] > 0
    assert metrics["max_drawdown"] <= 0
    assert len(walk_forward_folds(120, train_size=60, test_size=20)) == 3

    decision = evaluate_admission(
        metrics,
        deflated_sharpe=0.99,
        regimes=2,
        criteria=AdmissionCriteria(min_trades=5),
    )
    if not decision.admitted:
        assert registry.get("baseline.trend").status is StrategyStatus.DRAFT
    else:
        registry.transition("baseline.trend", StrategyStatus.BACKTESTED)