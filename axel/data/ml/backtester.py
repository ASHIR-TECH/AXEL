"""
Event-driven backtester with explicit, no-lookahead execution.

Guarantees, in order of importance:

1. **No look-ahead.** Decisions for time ``t`` only see bars with
   ``available_at <= t``, and the resulting target is executed at the symbol's
   next bar *open*, never at ``t``. The one-bar gap between decide and execute is
   the structural guarantee, not a convention.
2. **Session clock.** With a ``TradingCalendar`` the loop skips non-sessions and
   measures execution delay in *sessions*, so a Friday decision that fills on
   Monday is a recorded session gap rather than an invisible assumption.
3. **Always charge costs.** Commissions and slippage are applied on every fill
   including liquidations, so simulated PnL is honest.
4. **Real risk units.** ``risk_model`` sizes R by volatility or stop distance
   instead of a fixed percentage of notional.
5. **Corporate actions.** Cash dividends credit cash, and a delisting force-
   closes the position at its terminal price -- the symbol is never silently
   dropped, which is how survivorship bias enters a backtest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import floor
from typing import Protocol

from axel.data.ml.calendars import TradingCalendar, calendar_for_bars
from axel.data.ml.corporate_actions import (
    Delisting,
    Dividend,
    TerminalPricePolicy,
    delistings_by_symbol,
    dividend_cash,
    dividends_by_symbol,
)
from axel.data.ml.costs import ZERO_COSTS, CostModel
from axel.data.ml.metrics import summarize
from axel.data.ml.risk_model import RiskModel
from axel.data.ml.slippage import FixedBpsSlippage, SlippageModel
from axel.data.schemas import BarRecord
from axel.risk.tail import (
    Position as TailPosition,
)
from axel.risk.tail import (
    ProposedTrade,
    TailRiskEngine,
)


class DividendMode(str, Enum):
    """Whether dividends are credited as cash or already baked into prices."""

    NONE = "none"
    """Caller used total-return-adjusted bars; do not credit cash (default)."""

    CASH = "cash"
    """Raw prices; credit dividend cash on each ex-date."""


class ExitReason(str, Enum):
    SIGNAL = "signal"
    LIQUIDATION = "liquidation"
    DELISTING = "delisting"


class TargetStrategy(Protocol):
    """Returns a target exposure (fraction of equity, signed) per symbol."""

    def decide(
        self, timestamp: datetime, history: Mapping[str, Sequence[BarRecord]]
    ) -> Mapping[str, float]: ...


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    quantity: float
    entry_price: float
    exit_price: float
    gross_pnl: float
    costs: float
    pnl: float
    risk: float = 0.0
    exit_reason: ExitReason = ExitReason.SIGNAL

    @property
    def r_multiple(self) -> float:
        return self.pnl / self.risk if self.risk > 0 else 0.0


@dataclass(frozen=True)
class Fill:
    timestamp: datetime
    symbol: str
    quantity: float
    price: float
    commission: float
    slippage: float


@dataclass(frozen=True)
class BacktestResult:
    starting_equity: float
    equity_curve: tuple[tuple[datetime, float], ...]
    fills: tuple[Fill, ...]
    trades: tuple[Trade, ...]
    total_costs: float
    dividends_received: float = 0.0
    delisted_symbols: tuple[str, ...] = ()
    execution_delays: tuple[int, ...] = ()
    delay_unit: str = "bar"
    calendar: TradingCalendar | None = None

    @property
    def ending_equity(self) -> float:
        return self.equity_curve[-1][1] if self.equity_curve else self.starting_equity

    @property
    def mean_execution_delay(self) -> float:
        if not self.execution_delays:
            return 0.0
        return sum(self.execution_delays) / len(self.execution_delays)

    def metrics(self) -> dict[str, float]:
        result = summarize(self.equity_curve, self.trades, calendar=self.calendar)
        result["mean_execution_delay"] = self.mean_execution_delay
        result["max_execution_delay"] = float(max(self.execution_delays, default=0))
        result["dividends_received"] = self.dividends_received
        result["delisted"] = float(len(self.delisted_symbols))
        return result


@dataclass
class _Position:
    quantity: float = 0.0
    avg_price: float = 0.0
    entry_time: datetime | None = None
    open_cost: float = 0.0
    risk: float = 0.0


class Backtester:
    def __init__(
        self,
        *,
        starting_equity: float = 100_000.0,
        costs: CostModel = ZERO_COSTS,
        slippage: SlippageModel | None = None,
        lot_size: float = 1.0,
        risk_per_trade: float = 0.0,
        liquidate_at_end: bool = True,
        calendar: TradingCalendar | None = None,
        infer_calendar: bool = True,
        risk_model: RiskModel | None = None,
        risk_engine: TailRiskEngine | None = None,
    ) -> None:
        if starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        if lot_size < 0 or risk_per_trade < 0:
            raise ValueError("lot_size and risk_per_trade cannot be negative")
        self.starting_equity = starting_equity
        self.costs = costs
        self.slippage = slippage or FixedBpsSlippage(bps=0.0)
        self.lot_size = lot_size
        self.risk_per_trade = risk_per_trade
        self.liquidate_at_end = liquidate_at_end
        self.calendar = calendar
        # With no explicit calendar, infer one from the venues actually present so
        # a real feed is not silently treated as weekdays-only.
        self.infer_calendar = infer_calendar
        self.risk_model = risk_model
        self.risk_engine = risk_engine

    def run(
        self,
        bars_by_symbol: Mapping[str, Sequence[BarRecord]],
        strategy: TargetStrategy,
        *,
        dividends: Sequence[Dividend] = (),
        delistings: Sequence[Delisting] = (),
        dividend_mode: DividendMode = DividendMode.NONE,
    ) -> BacktestResult:
        series = {
            symbol: sorted(bars, key=lambda bar: bar.event_time)
            for symbol, bars in bars_by_symbol.items()
            if bars
        }
        dividend_map = dividends_by_symbol(dividends)
        delisting_map = delistings_by_symbol(delistings)
        calendar = self.calendar
        if calendar is None and self.infer_calendar:
            calendar = calendar_for_bars(
                bar for bars in series.values() for bar in bars
            )

        timeline: set[datetime] = {
            bar.event_time for bars in series.values() for bar in bars
        }
        if dividend_mode is DividendMode.CASH:
            for symbol, items in dividend_map.items():
                if symbol in series:
                    timeline.update(
                        datetime.combine(item.ex_date, datetime.min.time()).replace(
                            tzinfo=series[symbol][0].event_time.tzinfo
                        )
                        for item in items
                        if item.ex_date
                        <= series[symbol][-1].event_time.date()
                    )
        schedule = sorted(
            stamp
            for stamp in timeline
            if calendar is None or calendar.is_trading_day(stamp.date())
        )

        pointers = dict.fromkeys(series, 0)
        history: dict[str, list[BarRecord]] = {symbol: [] for symbol in series}
        last_price: dict[str, float] = {}
        positions: dict[str, _Position] = {}
        dead: set[str] = set()
        cash = self.starting_equity
        cost_total = [0.0]
        dividends_received = [0.0]
        dividend_cursor = {
            symbol: 0 for symbol, items in dividend_map.items() if items
        }
        delisted: set[str] = set()
        fills: list[Fill] = []
        trades: list[Trade] = []
        curve: list[tuple[datetime, float]] = []
        pending: dict[str, float] = {}
        decided_at: dict[str, datetime] = {}
        delays: list[int] = []
        asset_returns: dict[str, list[float]] = {symbol: [] for symbol in series}

        for timestamp in schedule:
            cash = self._credit_dividends(
                timestamp,
                dividend_mode,
                dividend_map,
                dividend_cursor,
                positions,
                dead,
                cash,
                dividends_received,
            )
            for symbol, bars in series.items():
                if symbol in dead:
                    continue
                index = pointers[symbol]
                # Drop bars the calendar skipped (a feed that carries weekend or
                # holiday bars must not wedge the pointer and stall the symbol).
                while index < len(bars) and bars[index].event_time < timestamp:
                    index += 1
                pointers[symbol] = index
                if index >= len(bars) or bars[index].event_time != timestamp:
                    continue
                bar = bars[index]
                pointers[symbol] += 1
                if symbol in pending:
                    exposure = pending.pop(symbol)
                    equity = self._equity(
                        cash, positions, last_price, override=(symbol, bar.open)
                    )
                    if symbol in decided_at:
                        delays.append(
                            self._delay(decided_at.pop(symbol), timestamp, calendar)
                        )
                    cash = self._execute(
                        symbol,
                        bar,
                        exposure,
                        equity,
                        timestamp,
                        cash,
                        positions,
                        trades,
                        fills,
                        cost_total,
                        history[symbol],
                        asset_returns if self.risk_engine is not None else None,
                    )
                history[symbol].append(bar)
                previous_close = last_price.get(symbol)
                last_price[symbol] = bar.close
                if previous_close:
                    asset_returns[symbol].append(bar.close / previous_close - 1.0)

            cash = self._settle_delistings(
                timestamp,
                delisting_map,
                dead,
                delisted,
                positions,
                last_price,
                cash,
                trades,
                fills,
                cost_total,
                series,
            )
            if pending:
                for symbol in list(pending):
                    if symbol in dead:
                        pending.pop(symbol, None)
                        decided_at.pop(symbol, None)
            curve.append((timestamp, self._equity(cash, positions, last_price)))
            available = {
                symbol: tuple(bar for bar in bars if bar.available_at <= timestamp)
                for symbol, bars in history.items()
                if symbol not in dead
            }
            targets = strategy.decide(timestamp, available)
            pending = {
                symbol: float(targets.get(symbol, 0.0))
                for symbol in series
                if symbol not in dead
            }
            decided_at = {symbol: timestamp for symbol in pending}

        final_time = schedule[-1] if schedule else None
        if self.liquidate_at_end and final_time is not None:
            cash = self._liquidate(
                series, positions, last_price, final_time, cash, trades, fills, cost_total
            )
            if curve:
                curve[-1] = (curve[-1][0], self._equity(cash, positions, last_price))

        return BacktestResult(
            starting_equity=self.starting_equity,
            equity_curve=tuple(curve),
            fills=tuple(fills),
            trades=tuple(trades),
            total_costs=cost_total[0],
            dividends_received=dividends_received[0],
            delisted_symbols=tuple(sorted(delisted)),
            execution_delays=tuple(delays),
            delay_unit="session" if calendar is not None else "bar",
            calendar=calendar,
        )

    def _delay(
        self, decided: datetime, executed: datetime, calendar: TradingCalendar | None
    ) -> int:
        """
        Sessions between decision and fill.

        With a calendar this is the exchange's session count, so a Friday
        decision filling on Monday reads 1 -- one session was skipped. Without
        one it is the bar-count delay,
        which is the only thing knowable -- the result records which unit was
        used in ``delay_unit`` so the metric is never mislabelled.
        """
        if executed <= decided:
            return 0
        if calendar is None:
            return 1
        return calendar.sessions_between(decided.date(), executed.date())

    def _credit_dividends(
        self,
        timestamp: datetime,
        mode: DividendMode,
        dividend_map: Mapping[str, Sequence[Dividend]],
        cursor: dict[str, int],
        positions: Mapping[str, _Position],
        dead: set[str],
        cash: float,
        received: list[float],
    ) -> float:
        """Credit ex-date dividends to holders; returns the updated cash balance."""
        if mode is not DividendMode.CASH:
            return cash
        today = timestamp.date()
        for symbol, items in dividend_map.items():
            index = cursor.get(symbol, 0)
            while index < len(items) and items[index].ex_date <= today:
                dividend = items[index]
                position = positions.get(symbol)
                if position is not None and position.quantity != 0 and symbol not in dead:
                    cash += dividend_cash(position.quantity, dividend.amount)
                    received[0] += dividend_cash(position.quantity, dividend.amount)
                index += 1
            cursor[symbol] = index
        return cash

    def _settle_delistings(
        self,
        timestamp: datetime,
        delisting_map: Mapping[str, Sequence[Delisting]],
        dead: set[str],
        delisted: set[str],
        positions: dict[str, _Position],
        last_price: Mapping[str, float],
        cash: float,
        trades: list[Trade],
        fills: list[Fill],
        cost_total: list[float],
        series: Mapping[str, Sequence[BarRecord]],
    ) -> float:
        """
        Force-close names that stopped trading; returns the updated cash balance.

        Proceeds must be returned rather than mutated in place, otherwise a
        delisting would close the position without ever crediting the sale.
        """
        today = timestamp.date()
        for symbol, events in delisting_map.items():
            if symbol in dead or symbol not in series:
                continue
            for event in events:
                if event.delisted_on > today:
                    continue
                position = positions.get(symbol)
                if position is not None and position.quantity != 0:
                    cash = self._close_position(
                        symbol,
                        event.terminal_price(last_price.get(symbol)),
                        event.terminal_price_policy is not TerminalPricePolicy.MARK_TO_ZERO,
                        timestamp,
                        cash,
                        positions,
                        trades,
                        fills,
                        cost_total,
                        ExitReason.DELISTING,
                    )
                dead.add(symbol)
                delisted.add(symbol)
                last_price.pop(symbol, None)
        return cash

    def _min_trade(self) -> float:
        return self.lot_size if self.lot_size > 0 else 1e-12

    def _round_lot(self, quantity: float) -> float:
        if self.lot_size <= 0:
            return quantity
        steps = floor(abs(quantity) / self.lot_size)
        return steps * self.lot_size * (1 if quantity >= 0 else -1)

    def _equity(
        self,
        cash: float,
        positions: Mapping[str, _Position],
        last_price: Mapping[str, float],
        *,
        override: tuple[str, float] | None = None,
    ) -> float:
        total = cash
        for symbol, position in positions.items():
            if position.quantity == 0:
                continue
            if override is not None and override[0] == symbol:
                price = override[1]
            else:
                price = last_price.get(symbol, 0.0)
            total += position.quantity * price
        return total

    def _risk_for(
        self,
        symbol: str,
        quantity: float,
        price: float,
        equity: float,
        history: Sequence[BarRecord],
    ) -> float:
        if self.risk_model is not None:
            return self.risk_model.risk(
                symbol=symbol, quantity=quantity, price=price, history=history
            )
        return equity * self.risk_per_trade

    def _apply_risk_limits(
        self,
        symbol: str,
        target: float,
        price: float,
        equity: float,
        positions: Mapping[str, _Position],
        asset_returns: Mapping[str, Sequence[float]] | None,
    ) -> float:
        """
        Clamp a target quantity through the ex-ante tail-risk budget.

        The strategy proposes; the risk engine disposes. Without this step the
        portfolio limits in ``docs/AXEL_quant_research_pack.md`` would exist only
        as a report nobody is forced to obey.
        """
        if self.risk_engine is None or asset_returns is None or price <= 0:
            return target
        delta = target - positions.get(symbol, _Position()).quantity
        if delta == 0:
            return target
        # The book is passed whole, including this name: the engine nets caps
        # against what is already held, which is what stops repeated small adds
        # from compounding past a single-name ceiling.
        book = [
            TailPosition(name, position.quantity, price, "unknown")
            for name, position in positions.items()
            if position.quantity != 0
        ]
        decision = self.risk_engine.calculate_position_limit(
            ProposedTrade(symbol, delta, price),
            book,
            equity=equity,
            asset_returns=asset_returns,
        )
        permitted = positions.get(symbol, _Position()).quantity + decision.approved_quantity
        return self._round_lot(permitted)

    def _execute(
        self,
        symbol: str,
        bar: BarRecord,
        exposure: float,
        equity: float,
        timestamp: datetime,
        cash: float,
        positions: dict[str, _Position],
        trades: list[Trade],
        fills: list[Fill],
        cost_total: list[float],
        history: Sequence[BarRecord],
        asset_returns: Mapping[str, Sequence[float]] | None = None,
    ) -> float:
        reference = bar.open
        if reference <= 0:
            return cash
        target = self._round_lot(exposure * equity / reference)
        target = self._apply_risk_limits(
            symbol, target, reference, equity, positions, asset_returns
        )
        position = positions.get(symbol, _Position())
        delta = target - position.quantity
        if abs(delta) < self._min_trade():
            return cash
        side = 1 if delta > 0 else -1
        price = self.slippage.adjust(reference, delta, side=side, bar=bar)
        commission = self.costs.commission(delta * price)
        cash -= delta * price + commission
        cost_total[0] += commission
        fills.append(
            Fill(
                timestamp=timestamp,
                symbol=symbol,
                quantity=delta,
                price=price,
                commission=commission,
                slippage=abs(price - reference) * abs(delta),
            )
        )
        self._book(
            symbol,
            delta,
            price,
            commission,
            timestamp,
            positions,
            trades,
            equity,
            history,
        )
        return cash

    def _book(
        self,
        symbol: str,
        delta: float,
        price: float,
        commission: float,
        timestamp: datetime,
        positions: dict[str, _Position],
        trades: list[Trade],
        equity: float,
        history: Sequence[BarRecord],
        exit_reason: ExitReason = ExitReason.SIGNAL,
    ) -> None:
        position = positions.setdefault(symbol, _Position())
        total = abs(delta)
        if total == 0:
            return
        remaining = delta
        if position.quantity != 0 and position.quantity * remaining < 0:
            close_quantity = min(abs(remaining), abs(position.quantity))
            sign = 1 if position.quantity > 0 else -1
            share = close_quantity / total
            gross = (price - position.avg_price) * close_quantity * sign
            cost = position.open_cost * share + commission * share
            risk = position.risk * share
            trades.append(
                Trade(
                    symbol=symbol,
                    entry_time=position.entry_time or timestamp,
                    exit_time=timestamp,
                    quantity=close_quantity * sign,
                    entry_price=position.avg_price,
                    exit_price=price,
                    gross_pnl=gross,
                    costs=cost,
                    pnl=gross - cost,
                    risk=risk,
                    exit_reason=exit_reason,
                )
            )
            position.open_cost -= position.open_cost * share
            position.risk -= risk
            position.quantity -= close_quantity * sign
            remaining -= close_quantity * (1 if delta > 0 else -1)
            if position.quantity == 0:
                position.avg_price = 0.0
                position.entry_time = None
        if remaining != 0:
            share = abs(remaining) / total
            open_commission = commission * share
            open_risk = (
                self._risk_for(symbol, abs(remaining), price, equity, history) * share
            )
            if position.quantity == 0:
                position.avg_price = price
                position.entry_time = timestamp
                position.open_cost = open_commission
                position.risk = open_risk
            else:
                weight = abs(position.quantity) + abs(remaining)
                position.avg_price = (
                    position.avg_price * abs(position.quantity) + price * abs(remaining)
                ) / weight
                position.open_cost += open_commission
                position.risk += open_risk
            position.quantity += remaining

    def _close_position(
        self,
        symbol: str,
        price: float,
        charge_costs: bool,
        timestamp: datetime,
        cash: float,
        positions: dict[str, _Position],
        trades: list[Trade],
        fills: list[Fill],
        cost_total: list[float],
        reason: ExitReason,
    ) -> float:
        position = positions.get(symbol)
        if position is None or position.quantity == 0:
            return cash
        fills.append(
            Fill(
                timestamp=timestamp,
                symbol=symbol,
                quantity=-position.quantity,
                price=price,
                commission=0.0,
                slippage=0.0,
            )
        )
        commission = self.costs.commission(position.quantity * price) if charge_costs else 0.0
        cash += position.quantity * price - commission
        cost_total[0] += commission
        self._book(
            symbol,
            -position.quantity,
            price,
            commission,
            timestamp,
            positions,
            trades,
            0.0,
            (),
            reason,
        )
        return cash

    def _liquidate(
        self,
        series: Mapping[str, Sequence[BarRecord]],
        positions: dict[str, _Position],
        last_price: Mapping[str, float],
        timestamp: datetime,
        cash: float,
        trades: list[Trade],
        fills: list[Fill],
        cost_total: list[float],
    ) -> float:
        for symbol, position in list(positions.items()):
            if position.quantity == 0:
                continue
            reference = last_price.get(symbol)
            if reference is None or reference <= 0:
                continue
            side = -1 if position.quantity > 0 else 1
            price = self.slippage.adjust(reference, position.quantity, side=side, bar=series[symbol][-1])
            commission = self.costs.commission(position.quantity * price)
            cash += position.quantity * price - commission
            cost_total[0] += commission
            fills.append(
                Fill(
                    timestamp=timestamp,
                    symbol=symbol,
                    quantity=-position.quantity,
                    price=price,
                    commission=commission,
                    slippage=abs(price - reference) * abs(position.quantity),
                )
            )
            self._book(
                symbol,
                -position.quantity,
                price,
                commission,
                timestamp,
                positions,
                trades,
                0.0,
                (),
                ExitReason.LIQUIDATION,
            )
        return cash


__all__ = [
    "BacktestResult",
    "Backtester",
    "DividendMode",
    "ExitReason",
    "Fill",
    "TargetStrategy",
    "Trade",
]