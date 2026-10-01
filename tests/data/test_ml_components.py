from datetime import UTC, date, datetime

import pytest

from axel.data.ml import (
    NO_SLIPPAGE,
    CostModel,
    FixedBpsSlippage,
    Split,
    TradingCalendar,
    VolumeImpactSlippage,
    apply_splits,
    max_drawdown,
    profit_factor,
    sharpe,
    total_return,
)
from axel.data.schemas import BarRecord, Provenance

START = datetime(2026, 1, 1, tzinfo=UTC)


def _bar(day: int, close: float, volume: float = 100.0) -> BarRecord:
    event = datetime(2026, 1, day, tzinfo=UTC)
    return BarRecord(
        symbol="AAPL",
        venue="alpaca-iex",
        event_time=event,
        available_at=event,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        adjustment_status="raw",
        provenance=Provenance(
            source="test", source_id=str(day), source_hash=f"h{day}", ingestion_timestamp=START
        ),
    )


def test_cost_model_commission_and_floor() -> None:
    assert CostModel(commission_bps=10).commission(10_000) == pytest.approx(10.0)
    assert CostModel(commission_bps=1, min_commission=5).commission(1_000) == pytest.approx(5.0)


def test_slippage_is_side_aware() -> None:
    bar = _bar(2, 100.0)
    buy = FixedBpsSlippage(bps=10).adjust(100.0, 1.0, side=1, bar=bar)
    sell = FixedBpsSlippage(bps=10).adjust(100.0, 1.0, side=-1, bar=bar)
    assert buy == pytest.approx(100.1)
    assert sell == pytest.approx(99.9)
    assert NO_SLIPPAGE.adjust(100.0, 1.0, side=1, bar=bar) == 100.0


def test_volume_impact_increases_with_participation() -> None:
    bar = _bar(2, 100.0, volume=1000.0)
    model = VolumeImpactSlippage(coefficient=0.1)
    small = model.adjust(100.0, 10.0, side=1, bar=bar)
    large = model.adjust(100.0, 1000.0, side=1, bar=bar)
    assert large > small > 100.0


def test_calendar_handles_weekends_and_holidays() -> None:
    calendar = TradingCalendar(holidays=[date(2026, 1, 1)])
    assert not calendar.is_trading_day(date(2026, 1, 1))
    assert calendar.is_trading_day(date(2026, 1, 2))
    assert not calendar.is_trading_day(date(2026, 1, 3))
    assert calendar.next_trading_day(date(2025, 12, 31)) == date(2026, 1, 2)


def test_splits_adjust_and_are_idempotent() -> None:
    bars = [_bar(1, 400.0, volume=100.0), _bar(10, 400.0, volume=100.0)]
    split = Split(symbol="AAPL", effective_date=date(2026, 6, 1), ratio=4.0)
    adjusted = apply_splits(bars, [split])
    assert adjusted[0].close == pytest.approx(100.0)
    assert adjusted[0].volume == pytest.approx(400.0)
    assert adjusted[0].adjustment_status == "split_adjusted"
    assert apply_splits(adjusted, [split]) == adjusted


def test_splits_ignore_pre_effective_bars() -> None:
    bars = [_bar(10, 400.0)]
    split = Split(symbol="AAPL", effective_date=date(2026, 1, 5), ratio=4.0)
    adjusted = apply_splits(bars, [split])
    assert adjusted[0].close == pytest.approx(400.0)
    assert adjusted[0].adjustment_status == "raw"


def test_metrics_from_equity_curve() -> None:
    curve = [
        (datetime(2026, 1, 1, tzinfo=UTC), 100.0),
        (datetime(2026, 1, 2, tzinfo=UTC), 110.0),
        (datetime(2026, 1, 3, tzinfo=UTC), 99.0),
    ]
    assert total_return(curve) == pytest.approx(-0.01)
    assert max_drawdown(curve) == pytest.approx(-0.1)
    assert isinstance(sharpe(curve), float)


def test_profit_factor_from_trades() -> None:
    class Trade:
        def __init__(self, pnl: float) -> None:
            self.pnl = pnl

    assert profit_factor([Trade(10.0), Trade(-5.0)]) == pytest.approx(2.0)
    assert profit_factor([]) == 0.0
