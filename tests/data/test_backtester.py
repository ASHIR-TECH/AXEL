from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from axel.data.ml import NO_SLIPPAGE, US_EQUITY_CALENDAR, Backtester, CostModel
from axel.data.schemas import BarRecord, Provenance

START = datetime(2026, 1, 5, tzinfo=UTC)  # first 2026 session


def _bars(
    symbol: str = "AAPL",
    *,
    count: int = 10,
    base: float = 100.0,
    rate: float = 0.01,
    availability_delay: timedelta = timedelta(0),
) -> list[BarRecord]:
    """Session-dated bars, as a real US equity feed produces."""
    bars = []
    event = START
    for index in range(count):
        while not US_EQUITY_CALENDAR.is_trading_day(event.date()):
            event += timedelta(days=1)
        price = base * (1.0 + rate) ** index
        bars.append(
            BarRecord(
                symbol=symbol,
                venue="alpaca-iex",
                event_time=event,
                available_at=event + availability_delay,
                open=price,
                high=price,
                low=price,
                close=price,
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


class LongAfterFirstBar:
    def decide(
        self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
    ) -> Mapping[str, float]:
        return {symbol: (1.0 if history[symbol] else 0.0) for symbol in history}


def _backtester(**kwargs) -> Backtester:
    return Backtester(slippage=NO_SLIPPAGE, **kwargs)


def test_executes_at_next_bar_open() -> None:
    bars = _bars(count=5)
    result = _backtester().run({"AAPL": bars}, LongAfterFirstBar())
    assert result.fills
    assert result.fills[0].timestamp == bars[1].event_time
    assert result.ending_equity > result.starting_equity


def test_respects_available_at_when_deciding() -> None:
    bars = _bars(count=6, availability_delay=timedelta(days=1))
    result = _backtester().run({"AAPL": bars}, LongAfterFirstBar())
    assert result.fills
    assert result.fills[0].timestamp == bars[2].event_time


def test_costs_reduce_ending_equity() -> None:
    bars = _bars(count=20)
    free = _backtester().run({"AAPL": bars}, LongAfterFirstBar())
    costly = _backtester(costs=CostModel(commission_bps=50)).run(
        {"AAPL": bars}, LongAfterFirstBar()
    )
    assert costly.total_costs > 0
    assert costly.ending_equity < free.ending_equity


def test_round_trip_trade_records_risk_and_r_multiple() -> None:
    bars = _bars(count=8)

    class LongThenFlat:
        def decide(
            self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
        ) -> Mapping[str, float]:
            return {
                symbol: (1.0 if len(history[symbol]) <= 2 else 0.0) for symbol in history
            }

    result = _backtester(risk_per_trade=0.01).run({"AAPL": bars}, LongThenFlat())
    assert result.trades
    trade = result.trades[0]
    assert trade.entry_time < trade.exit_time
    assert trade.risk > 0
    assert trade.r_multiple == trade.pnl / trade.risk


def test_multi_symbol_portfolio_tracks_union_timeline() -> None:
    result = _backtester().run(
        {"AAPL": _bars("AAPL", count=5), "MSFT": _bars("MSFT", count=5, base=200.0)},
        LongAfterFirstBar(),
    )
    assert len(result.equity_curve) == 5
    assert {fill.symbol for fill in result.fills} == {"AAPL", "MSFT"}
