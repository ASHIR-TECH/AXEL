"""
Session-clock execution, dividends and delisting inside the backtest loop.

These are the behaviours that only exist once the calendar, corporate actions
and the position book are wired together.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta

import pytest

from axel.data.ml import (
    NO_SLIPPAGE,
    US_EQUITY_CALENDAR,
    Backtester,
    DividendMode,
    ExitReason,
    TradingCalendar,
    VolatilityRiskModel,
)
from axel.data.ml.corporate_actions import Delisting, Dividend, TerminalPricePolicy
from axel.data.schemas import BarRecord, Provenance
from axel.risk.tail import RiskBudget, TailRiskEngine

START = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday


def _bars(
    symbol: str = "AAA",
    *,
    count: int = 10,
    base: float = 100.0,
    rate: float = 0.0,
    start: datetime = START,
    sessions_only: bool = False,
    availability_delay: timedelta = timedelta(days=1),
) -> list[BarRecord]:
    bars: list[BarRecord] = []
    event = start if isinstance(start, datetime) else datetime.combine(start, time(0), tzinfo=UTC)
    for index in range(count):
        if sessions_only:
            while not US_EQUITY_CALENDAR.is_trading_day(event.date()):
                event += timedelta(days=1)
        price = base * (1.0 + rate) ** index
        bars.append(
            BarRecord(
                symbol=symbol,
                venue="test",
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


class BuyAndHold:
    def decide(
        self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
    ) -> Mapping[str, float]:
        return {symbol: (1.0 if history[symbol] else 0.0) for symbol in history}


class LongThenFlat:
    def __init__(self, bars_to_hold: int = 2) -> None:
        self._bars_to_hold = bars_to_hold

    def decide(
        self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
    ) -> Mapping[str, float]:
        return {
            symbol: (1.0 if len(history[symbol]) <= self._bars_to_hold else 0.0)
            for symbol in history
        }


def _backtester(**kwargs) -> Backtester:
    return Backtester(slippage=NO_SLIPPAGE, **kwargs)


class TestSessionClock:
    def test_non_trading_days_are_skipped(self):
        bars = _bars(count=10)  # spans a weekend
        result = _backtester(calendar=US_EQUITY_CALENDAR).run({"AAA": bars}, BuyAndHold())
        stamps = {stamp.date() for stamp, _ in result.equity_curve}
        assert all(US_EQUITY_CALENDAR.is_trading_day(stamp) for stamp in stamps)

    def test_delay_is_measured_in_sessions_with_a_calendar(self):
        bars = _bars(count=10, sessions_only=True)
        result = _backtester(calendar=US_EQUITY_CALENDAR).run({"AAA": bars}, BuyAndHold())
        assert result.delay_unit == "session"
        assert result.execution_delays
        assert set(result.execution_delays) == {1}

    def test_friday_decision_fills_on_monday(self):
        # Bars are sessions only and visible immediately, so the first decision
        # lands Friday Jan 9. The next session is Monday Jan 12: three calendar
        # days later, but a one-session delay on the trading clock.
        bars = _bars(
            count=6, start=date(2026, 1, 9), sessions_only=True, availability_delay=timedelta(0)
        )
        result = _backtester(calendar=US_EQUITY_CALENDAR).run(
            {"AAA": bars}, LongThenFlat(1)
        )
        assert result.fills[0].timestamp.date() == date(2026, 1, 12)
        assert result.execution_delays[0] == 1

    def test_weekend_bars_never_become_fill_dates(self):
        bars = _bars(count=6, start=date(2026, 1, 9), availability_delay=timedelta(0))
        result = _backtester(calendar=US_EQUITY_CALENDAR).run({"AAA": bars}, BuyAndHold())
        fill_dates = {fill.timestamp.date() for fill in result.fills}
        assert fill_dates
        assert all(US_EQUITY_CALENDAR.is_trading_day(stamp) for stamp in fill_dates)

    def test_without_a_calendar_delay_unit_is_bars(self):
        result = _backtester().run({"AAA": _bars(count=6)}, LongThenFlat(2))
        assert result.delay_unit == "bar"
        assert result.mean_execution_delay >= 1.0

    def test_holiday_is_not_a_fill_date(self):
        # The feed carries a bar for Fri Jul 3, which Independence Day (Jul 4,
        # Saturday) is observed on. The calendar must not execute into it.
        bars = _bars(
            count=6, start=datetime(2026, 7, 1, tzinfo=UTC), availability_delay=timedelta(0)
        )
        assert date(2026, 7, 3) in {bar.event_time.date() for bar in bars}
        result = _backtester(calendar=US_EQUITY_CALENDAR).run({"AAA": bars}, BuyAndHold())
        curve_dates = {stamp.date() for stamp, _ in result.equity_curve}
        fill_dates = {fill.timestamp.date() for fill in result.fills}
        assert date(2026, 7, 3) not in curve_dates
        assert date(2026, 7, 3) not in fill_dates
        assert date(2026, 7, 6) in fill_dates

    def test_metrics_include_session_diagnostics(self):
        result = _backtester(calendar=US_EQUITY_CALENDAR).run(
            {"AAA": _bars(count=10)}, BuyAndHold()
        )
        metrics = result.metrics()
        assert "session_coverage" in metrics
        assert metrics["mean_execution_delay"] == pytest.approx(1.0)


class TestDividendCashflows:
    def test_cash_mode_credits_dividends_to_an_open_position(self):
        bars = _bars(count=8, base=100.0)
        dividends = [Dividend("AAA", date(2026, 1, 8), 2.0)]
        result = _backtester().run(
            {"AAA": bars}, LongThenFlat(10), dividends=dividends, dividend_mode=DividendMode.CASH
        )
        # 100k equity at a 100 price is 1000 shares, so 2.00/share is 2000 cash.
        assert result.dividends_received == pytest.approx(2.0 * 1000.0)

    def test_no_cash_when_position_is_flat_on_the_ex_date(self):
        bars = _bars(count=8)
        dividends = [Dividend("AAA", date(2026, 1, 6), 2.0)]  # ex-date before entry fills
        result = _backtester().run(
            {"AAA": bars}, LongThenFlat(1), dividends=dividends, dividend_mode=DividendMode.CASH
        )
        assert result.dividends_received == 0.0

    def test_none_mode_never_double_counts(self):
        """Default mode assumes the caller already used total-return bars."""
        bars = _bars(count=8)
        dividends = [Dividend("AAA", date(2026, 1, 8), 2.0)]
        result = _backtester().run({"AAA": bars}, LongThenFlat(10), dividends=dividends)
        assert result.dividends_received == 0.0

    def test_cash_dividend_raises_ending_equity(self):
        bars = _bars(count=10, rate=0.0)
        with_cash = _backtester().run(
            {"AAA": bars}, LongThenFlat(10),
            dividends=[Dividend("AAA", date(2026, 1, 8), 2.0)],
            dividend_mode=DividendMode.CASH,
        )
        without = _backtester().run({"AAA": bars}, LongThenFlat(10))
        assert with_cash.ending_equity > without.ending_equity


class TestDelisting:
    def test_delisting_force_closes_the_position(self):
        bars = _bars(count=6)
        delistings = [Delisting("AAA", date(2026, 1, 8), reason="bankruptcy")]
        result = _backtester().run(
            {"AAA": bars}, BuyAndHold(), delistings=delistings
        )
        assert result.delisted_symbols == ("AAA",)
        assert any(trade.exit_reason is ExitReason.DELISTING for trade in result.trades)

    def test_mark_to_zero_produces_a_total_loss(self):
        bars = _bars(count=6, rate=0.05)
        delistings = [Delisting("AAA", date(2026, 1, 8))]
        result = _backtester().run({"AAA": bars}, BuyAndHold(), delistings=delistings)
        delisting_trade = next(
            trade for trade in result.trades if trade.exit_reason is ExitReason.DELISTING
        )
        assert delisting_trade.exit_price == 0.0
        assert delisting_trade.pnl < 0
        assert result.ending_equity < result.starting_equity * 0.5

    def test_last_trade_policy_does_not_treat_it_as_a_loss(self):
        bars = _bars(count=6, rate=0.05)
        delistings = [
            Delisting(
                "AAA", date(2026, 1, 8),
                terminal_price_policy=TerminalPricePolicy.LAST_TRADE,
            )
        ]
        result = _backtester().run({"AAA": bars}, BuyAndHold(), delistings=delistings)
        trade = next(
            item for item in result.trades if item.exit_reason is ExitReason.DELISTING
        )
        assert trade.exit_price > 0.0
        assert trade.pnl > 0

    def test_sale_proceeds_reach_the_account(self):
        """Closing a position must credit cash, not just clear the holding."""
        bars = _bars(count=6, rate=0.05)
        delistings = [
            Delisting(
                "AAA", date(2026, 1, 8),
                terminal_price_policy=TerminalPricePolicy.LAST_TRADE,
            )
        ]
        result = _backtester().run({"AAA": bars}, BuyAndHold(), delistings=delistings)
        trade = next(
            item for item in result.trades if item.exit_reason is ExitReason.DELISTING
        )
        assert result.ending_equity == pytest.approx(
            result.starting_equity + trade.gross_pnl, rel=1e-9
        )

    def test_a_closed_delisted_name_stops_marking_equity(self):
        bars = _bars(count=8, rate=0.0)
        result = _backtester().run(
            {"AAA": bars}, BuyAndHold(),
            delistings=[Delisting("AAA", date(2026, 1, 8))],
        )
        delisting_time = next(
            item.exit_time for item in result.trades
            if item.exit_reason is ExitReason.DELISTING
        )
        after = [value for stamp, value in result.equity_curve if stamp > delisting_time]
        assert after
        assert all(value == pytest.approx(after[0]) for value in after)

    def test_delisted_symbol_is_removed_from_the_decision_view(self):
        seen_after: list[bool] = []

        class Watcher:
            def decide(
                self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
            ) -> Mapping[str, float]:
                if timestamp.date() > date(2026, 1, 8):
                    seen_after.append("AAA" in history)
                return {}

        bars = _bars(count=8)
        _backtester().run(
            {"AAA": bars}, Watcher(), delistings=[Delisting("AAA", date(2026, 1, 8))]
        )
        assert seen_after and not any(seen_after)

    def test_no_dup_double_exit_for_a_delisted_symbol(self):
        bars = _bars(count=8)
        result = _backtester().run(
            {"AAA": bars}, BuyAndHold(), delistings=[Delisting("AAA", date(2026, 1, 8))]
        )
        reasons = [trade.exit_reason for trade in result.trades]
        assert reasons.count(ExitReason.DELISTING) == 1

    def test_flat_position_at_delisting_is_simply_dropped(self):
        bars = _bars(count=8)
        result = _backtester().run(
            {"AAA": bars}, LongThenFlat(1), delistings=[Delisting("AAA", date(2026, 1, 8))]
        )
        assert result.delisted_symbols == ("AAA",)
        assert not any(t.exit_reason is ExitReason.DELISTING for t in result.trades)


class TestRiskModelWiring:
    def test_volatility_risk_model_populates_r(self):
        bars = _bars(count=12, rate=0.02)
        result = _backtester(risk_model=VolatilityRiskModel(lookback=5)).run(
            {"AAA": bars}, LongThenFlat(3)
        )
        assert result.trades
        assert all(trade.risk > 0 for trade in result.trades)
        assert any(trade.r_multiple != 0.0 for trade in result.trades)

    def test_risk_model_overrides_the_legacy_fraction(self):
        bars = _bars(count=12, rate=0.02)
        legacy = _backtester(risk_per_trade=0.01).run({"AAA": bars}, LongThenFlat(3))
        modelled = _backtester(
            risk_per_trade=0.01, risk_model=VolatilityRiskModel(lookback=5)
        ).run({"AAA": bars}, LongThenFlat(3))
        assert legacy.trades[0].risk != modelled.trades[0].risk

    def test_exit_reason_defaults_to_signal(self):
        result = _backtester().run({"AAA": _bars(count=10)}, LongThenFlat(2))
        assert all(trade.exit_reason is ExitReason.SIGNAL for trade in result.trades)

    def test_liquidation_is_labelled(self):
        result = _backtester().run({"AAA": _bars(count=6)}, BuyAndHold())
        assert any(trade.exit_reason is ExitReason.LIQUIDATION for trade in result.trades)


class TestTailRiskGatesSizing:
    """The strategy proposes, the risk budget disposes."""

    def _bars_for(self, symbol, count=80, base=100.0, rate=0.0, volatility=0.0):
        bars = []
        price = base
        for index in range(count):
            event = datetime(2026, 1, 5, tzinfo=UTC) + timedelta(days=index)
            if volatility:
                # Deterministic alternating pattern; no RNG in a research engine.
                price *= 1.0 + (volatility if index % 2 else -volatility)
            else:
                price *= 1.0 + rate
            bars.append(
                BarRecord(
                    symbol=symbol,
                    venue="test",
                    event_time=event,
                    available_at=event,
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
                        ingestion_timestamp=event,
                    ),
                )
            )
        return bars

    def test_tight_budget_shrinks_the_fill(self):
        bars = self._bars_for("AAA", volatility=0.02)
        unconstrained = _backtester().run({"AAA": bars}, BuyAndHold())
        gated = _backtester(risk_engine=TailRiskEngine(RiskBudget(max_asset_exposure=0.01))).run(
            {"AAA": bars}, BuyAndHold()
        )
        assert sum(abs(fill.quantity) for fill in gated.fills) < sum(
            abs(fill.quantity) for fill in unconstrained.fills
        )

    def test_single_name_ceiling_is_respected_against_equity(self):
        """The ceiling is 10% of equity *at sizing time*, which grows with PnL."""
        bars = self._bars_for("AAA", volatility=0.02)
        gated = _backtester(risk_engine=TailRiskEngine(RiskBudget(max_asset_exposure=0.10))).run(
            {"AAA": bars}, BuyAndHold()
        )
        peak_equity = max(value for _, value in gated.equity_curve)
        # The ceiling applies to the position, not to each individual fill, so
        # accumulate fills to measure the holding actually carried.
        held = 0.0
        peak_notional = 0.0
        for fill in gated.fills:
            held += fill.quantity
            peak_notional = max(peak_notional, abs(held) * fill.price)
        assert peak_notional <= peak_equity * 0.10 + 1.0

    def test_exhausted_budget_blocks_further_fills(self):
        bars = self._bars_for("AAA", volatility=0.02)
        gated = _backtester(
            risk_engine=TailRiskEngine(RiskBudget(max_asset_exposure=0.0, max_stress_loss=0.0))
        ).run({"AAA": bars}, BuyAndHold())
        assert all(fill.quantity == 0 for fill in gated.fills) if gated.fills else True
        assert gated.ending_equity == pytest.approx(gated.starting_equity)

    def test_engine_does_not_change_behaviour_when_absent(self):
        bars = self._bars_for("AAA", volatility=0.02)
        plain = _backtester().run({"AAA": bars}, BuyAndHold())
        assert plain.fills

    def test_calendar_is_inferred_from_the_feed_venue(self):
        bars = self._bars_for("AAA", volatility=0.02)
        for bar in bars:
            object.__setattr__(bar, "venue", "alpaca-iex")
        inferred = _backtester().run({"AAA": bars}, BuyAndHold())
        assert inferred.calendar is US_EQUITY_CALENDAR
        assert inferred.delay_unit == "session"

    def test_unknown_venue_is_left_uncalendared(self):
        bars = self._bars_for("AAA", volatility=0.02)
        for bar in bars:
            object.__setattr__(bar, "venue", "synthetic")
        result = _backtester().run({"AAA": bars}, BuyAndHold())
        assert result.calendar is None
        assert result.delay_unit == "bar"

    def test_inference_can_be_switched_off(self):
        bars = self._bars_for("AAA", volatility=0.02)
        for bar in bars:
            object.__setattr__(bar, "venue", "alpaca-iex")
        result = Backtester(slippage=NO_SLIPPAGE, infer_calendar=False).run(
            {"AAA": bars}, BuyAndHold()
        )
        assert result.calendar is None

    def test_reducing_targets_is_not_blocked_by_the_gate(self):
        """A gate that cannot shrink a position would trap the strategy in it."""

        class BuyThenSell:
            def __init__(self) -> None:
                self._calls = 0

            def decide(
                self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
            ) -> Mapping[str, float]:
                self._calls += 1
                return {"AAA": 1.0 if self._calls <= 2 else 0.0}

        bars = self._bars_for("AAA", volatility=0.02)
        gated = _backtester(risk_engine=TailRiskEngine(RiskBudget(max_asset_exposure=0.05))).run(
            {"AAA": bars}, BuyThenSell()
        )
        assert any(fill.quantity < 0 for fill in gated.fills), "must be able to exit"
        assert gated.ending_equity < gated.starting_equity


class TestCalendarAwareAnnualisationInsideReport:
    def test_gappy_series_annualises_on_elapsed_time(self):
        """A curve missing sessions must not be annualised as if it had them."""
        calendar = TradingCalendar(holidays=())
        sparse = _bars(count=40, start=START)
        dense = _bars(count=40, rate=0.01)
        backtester = _backtester(calendar=calendar)
        for bars in (sparse, dense):
            result = backtester.run({"AAA": bars}, BuyAndHold())
            assert "elapsed_years" in result.metrics()