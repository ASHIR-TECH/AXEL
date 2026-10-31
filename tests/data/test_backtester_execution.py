from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from axel.data.marketdata import FundingRateObservation
from axel.data.ml import (
    NO_SLIPPAGE,
    US_EQUITY_CALENDAR,
    Backtester,
    CostModel,
    ExecutionModel,
    ExecutionPlan,
    FinancingModel,
    RejectionReason,
)
from axel.data.ml.costs import FinancingModel as FinancingModelFromCosts
from axel.data.schemas import BarRecord, Provenance

START = datetime(2026, 1, 5, tzinfo=UTC)  # first 2026 US equity session


def _bars(
    symbol: str = "AAPL",
    *,
    count: int = 6,
    price: float = 100.0,
    volume: float = 10_000.0,
    zero_volume_at: int | None = None,
) -> list[BarRecord]:
    bars = []
    event = START
    for index in range(count):
        while not US_EQUITY_CALENDAR.is_trading_day(event.date()):
            event += timedelta(days=1)
        bars.append(
            BarRecord(
                symbol=symbol,
                venue="alpaca-iex",
                event_time=event,
                available_at=event,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=0.0 if index == zero_volume_at else volume,
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


class LongAfterFirstBar:
    def decide(
        self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
    ) -> Mapping[str, float]:
        return {symbol: (1.0 if history[symbol] else 0.0) for symbol in history}


class ShortAfterFirstBar:
    def decide(
        self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
    ) -> Mapping[str, float]:
        return {symbol: (-1.0 if history[symbol] else 0.0) for symbol in history}


def _backtester(**kwargs) -> Backtester:
    return Backtester(slippage=NO_SLIPPAGE, **kwargs)


def _funding(
    event_time: datetime,
    *,
    rate: float = 0.01,
    mark_price: float | None = 100.0,
    available_at: datetime | None = None,
    instrument_id: str = "AAPL",
) -> FundingRateObservation:
    return FundingRateObservation(
        instrument_id=instrument_id,
        venue="binance",
        event_time=event_time,
        available_at=available_at or event_time,
        rate=rate,
        mark_price=mark_price,
        interval_hours=8.0,
        provenance=Provenance(
            source="test",
            source_id=f"funding-{event_time.isoformat()}",
            source_hash="funding",
            ingestion_timestamp=START,
        ),
    )


def test_positive_funding_is_charged_to_a_long_and_attributed_to_the_trade() -> None:
    bars = _bars(count=5)
    baseline = _backtester().run({"AAPL": bars}, LongAfterFirstBar())
    funded = _backtester().run(
        {"AAPL": bars},
        LongAfterFirstBar(),
        funding_rates=(_funding(bars[2].event_time),),
    )

    expected_payment = 1_000.0 * 100.0 * 0.01  # 1000 shares at the mark
    assert funded.funding_paid == pytest.approx(expected_payment)
    assert funded.ending_equity == pytest.approx(
        baseline.ending_equity - expected_payment
    )
    assert funded.trades
    assert sum(trade.carry for trade in funded.trades) == pytest.approx(expected_payment)
    assert funded.trades[0].pnl == pytest.approx(
        funded.trades[0].gross_pnl - funded.trades[0].costs - funded.trades[0].carry
    )
    assert funded.metrics()["total_carry"] == pytest.approx(expected_payment)


def test_negative_funding_credits_the_long() -> None:
    bars = _bars(count=5)
    baseline = _backtester().run({"AAPL": bars}, LongAfterFirstBar())
    funded = _backtester().run(
        {"AAPL": bars},
        LongAfterFirstBar(),
        funding_rates=(_funding(bars[2].event_time, rate=-0.01),),
    )
    assert funded.funding_paid < 0
    assert funded.ending_equity > baseline.ending_equity


def test_funding_that_arrives_after_the_event_fails_closed() -> None:
    bars = _bars(count=5)
    late = _funding(
        bars[2].event_time,
        available_at=bars[2].event_time + timedelta(days=1),
    )
    with pytest.raises(ValueError, match="point-in-time"):
        _backtester().run(
            {"AAPL": bars}, LongAfterFirstBar(), funding_rates=(late,)
        )


def test_long_financing_and_short_borrow_are_separate_costs() -> None:
    bars = _bars(count=5)
    baseline = _backtester().run({"AAPL": bars}, LongAfterFirstBar())

    financed = _backtester(
        financing=FinancingModel(long_financing_bps_per_year=36_500)
    ).run({"AAPL": bars}, LongAfterFirstBar())
    assert financed.financing_paid > 0
    assert financed.ending_equity < baseline.ending_equity
    assert sum(trade.carry for trade in financed.trades) == pytest.approx(
        financed.financing_paid
    )

    long_only = _backtester(
        financing=FinancingModel(long_financing_bps_per_year=36_500)
    ).run({"AAPL": bars}, ShortAfterFirstBar())
    assert long_only.financing_paid == 0.0

    borrowed = _backtester(
        financing=FinancingModel(short_borrow_bps_per_year=36_500)
    ).run({"AAPL": bars}, ShortAfterFirstBar())
    assert borrowed.financing_paid > 0


def test_partial_fills_are_capped_by_bar_participation() -> None:
    bars = _bars(count=5, volume=100.0)
    result = _backtester(
        execution=ExecutionModel(max_participation_rate=0.1),
        liquidate_at_end=False,
    ).run({"AAPL": bars}, LongAfterFirstBar())

    assert [fill.quantity for fill in result.fills] == [10.0, 10.0, 10.0, 10.0]
    first = result.fills[0]
    assert first.requested_quantity == pytest.approx(1_000.0)
    assert first.rejected_quantity == pytest.approx(990.0)
    assert first.rejection_reason is RejectionReason.PARTICIPATION_LIMIT
    assert result.rejections
    assert result.rejections[0].reason is RejectionReason.PARTICIPATION_LIMIT
    assert result.rejections[0].requested_quantity == pytest.approx(990.0)


def test_zero_volume_bar_rejects_instead_of_filling() -> None:
    bars = _bars(count=4, zero_volume_at=1)
    result = _backtester(
        execution=ExecutionModel(max_participation_rate=0.5),
        liquidate_at_end=False,
    ).run({"AAPL": bars}, LongAfterFirstBar())

    assert result.rejections
    assert result.rejections[0].reason is RejectionReason.NO_LIQUIDITY
    assert result.fills
    assert result.fills[0].timestamp == bars[2].event_time


def test_below_minimum_fill_is_rejected_not_rounded_up() -> None:
    bars = _bars(count=4, volume=100.0)
    result = _backtester(
        execution=ExecutionModel(
            max_participation_rate=0.01, min_fill_quantity=100.0
        ),
        liquidate_at_end=False,
    ).run({"AAPL": bars}, LongAfterFirstBar())

    assert result.fills == ()
    assert result.rejections
    assert result.rejections[0].reason is RejectionReason.BELOW_MINIMUM


def test_partial_fills_can_be_disabled() -> None:
    bars = _bars(count=4, volume=100.0)
    result = _backtester(
        execution=ExecutionModel(
            max_participation_rate=0.1, allow_partial_fills=False
        ),
        liquidate_at_end=False,
    ).run({"AAPL": bars}, LongAfterFirstBar())

    assert result.fills == ()
    assert result.rejections
    assert result.rejections[0].reason is RejectionReason.PARTICIPATION_LIMIT


def test_execution_assumptions_are_recorded_with_the_result() -> None:
    bars = _bars(count=4)
    result = _backtester(
        execution=ExecutionModel(max_participation_rate=0.25)
    ).run({"AAPL": bars}, LongAfterFirstBar())
    assumptions = dict(result.execution_assumptions)
    assert "decision_clock" in assumptions
    assert "fill_model" in assumptions
    assert "max_participation_rate=0.25" in assumptions["fill_model"]
    assert "funding" in assumptions
    assert "financing" in assumptions
    assert "liquidation" in assumptions
    assert "taxes" in assumptions
    assert "settlement" in assumptions


def test_partial_close_allocates_entry_cost_proportionally() -> None:
    bars = _bars(count=5)

    class LongThenHalfThenFlat:
        def decide(
            self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
        ) -> Mapping[str, float]:
            size = len(history["AAPL"])
            exposure = 1.0 if size <= 1 else (0.5 if size == 2 else 0.0)
            return {"AAPL": exposure}

    result = _backtester(
        costs=CostModel(commission_bps=10), liquidate_at_end=False
    ).run({"AAPL": bars}, LongThenHalfThenFlat())

    # Entry commission is 1000 * 100 * 10bps = 100. The first reduction is
    # 501 shares (the 0.5 target floors to 499), so it must carry 50.1% of the
    # entry commission and 50.1% of its own exit commission -- not all of it.
    assert len(result.trades) == 2
    first_quantity = abs(result.trades[0].quantity)
    second_quantity = abs(result.trades[1].quantity)
    assert first_quantity + second_quantity == pytest.approx(1_000.0)
    first_exit_commission = first_quantity * 100.0 * 10 / 10_000.0
    second_exit_commission = second_quantity * 100.0 * 10 / 10_000.0
    assert result.trades[0].costs == pytest.approx(
        100.0 * first_quantity / 1_000.0
        + first_exit_commission * first_quantity / 1_000.0
    )
    assert result.trades[1].costs == pytest.approx(
        100.0 * second_quantity / 1_000.0 + second_exit_commission
    )
    assert result.total_costs == pytest.approx(
        100.0 + first_exit_commission + second_exit_commission
    )


def test_zero_volume_with_participation_is_zero_capacity_even_when_not_rejected() -> None:
    bar = _bars(count=1, volume=0.0)[0]
    plan = ExecutionModel(
        max_participation_rate=0.1, reject_zero_volume=False
    ).plan(10.0, bar)
    assert plan.capacity == 0.0
    assert plan.filled_quantity == 0.0
    assert plan.rejection_reason is RejectionReason.NO_LIQUIDITY


def test_execution_model_and_financing_validation() -> None:
    with pytest.raises(ValueError, match="max_participation_rate"):
        ExecutionModel(max_participation_rate=0)
    with pytest.raises(ValueError, match="min_fill_quantity"):
        ExecutionModel(min_fill_quantity=-1)
    with pytest.raises(ValueError, match="financing rates"):
        FinancingModel(long_financing_bps_per_year=-1)
    with pytest.raises(ValueError, match="day_count"):
        FinancingModel(day_count=0)

    bar = _bars(count=1, volume=0.0)[0]
    plan = ExecutionModel(reject_zero_volume=False).plan(10.0, bar)
    assert plan == ExecutionPlan(
        requested_quantity=10.0, filled_quantity=10.0, rejection_reason=None, capacity=None
    )


def test_financing_accrual_uses_calendar_time_and_side() -> None:
    model = FinancingModelFromCosts(
        long_financing_bps_per_year=100.0,
        short_borrow_bps_per_year=500.0,
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=365)
    assert model.accrual(100.0, 50.0, start, end) == pytest.approx(50.0)
    assert model.accrual(-100.0, 50.0, start, end) == pytest.approx(250.0)
    assert model.accrual(100.0, 50.0, end, start) == 0.0
    assert model.accrual(0.0, 50.0, start, end) == 0.0
