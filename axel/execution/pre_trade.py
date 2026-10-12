"""
Deterministic pre-trade validator (Phase 6, spec §4.5 / §2.5).

The validator runs the ordered gate sequence the spec requires *before* any
order may be submitted:

1.  request schema and strategy/playbook eligibility;
2.  market session and symbol eligibility;
3.  market-data freshness;
4.  exposure / concentration caps;
5.  daily loss and drawdown;
6.  duplicate / stale / conflicting intent;
7.  kill-switch state;
8.  Watchdog health;
9.  authenticated manual approval (when required);
10. buying power;
11. the upstream deterministic risk decision.

It is fail-closed: a required input that is missing, stale, contradictory,
unparseable or unverifiable rejects a risk-increasing order. It invents **no**
numeric limit --- every cap comes from the reviewed :class:`LiveConfig` and the
caller-supplied freshness bounds.

Risk-reducing orders (a sell that lowers an existing long) are still schema /
session / freshness / duplicate / connectivity / approval checked, but bypass
the risk-increasing caps and a new-risk halt, so a halt never removes the
ability to reduce exposure (spec §4.8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from axel.core.clock import Clock, RealClock
from axel.core.contracts import Order, RiskDecision
from axel.core.money import to_decimal
from axel.core.types import OrderSide, OrderType, RiskVerdict, Section
from axel.execution.approval import compute_order_hash
from axel.execution.live_gate import LiveConfig
from axel.execution.reason_codes import PreTradeReason


@dataclass(frozen=True)
class OpenPosition:
    """A currently open position used for concentration/exposure maths."""

    symbol: str
    side: OrderSide
    qty: Decimal
    notional: Decimal


@dataclass(frozen=True)
class MarketSnapshot:
    """Latest market data for the traded symbol."""

    symbol: str
    last_price: Decimal
    as_of: datetime


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Account facts needed by the pre-trade gate."""

    total_equity: Decimal
    cash: Decimal
    buying_power: Decimal
    start_of_day_equity: Decimal
    realized_pnl_today: Decimal = Decimal(0)
    unrealized_pnl_today: Decimal = Decimal(0)


@dataclass(frozen=True)
class PreTradeContext:
    """All deterministic inputs required to decide a single order."""

    order: Order
    config: LiveConfig
    account: PortfolioSnapshot
    now: datetime
    market: MarketSnapshot | None = None
    open_positions: tuple[OpenPosition, ...] = ()
    recent_order_hashes: frozenset[str] = frozenset()
    recent_order_times: tuple[datetime, ...] = ()
    strategy_live_allowed: bool = False
    session_open: bool = False
    market_data_max_age_seconds: int | None = None
    intent_max_age_seconds: int | None = None
    kill_switch_active: bool = False
    watchdog_healthy: bool = False
    broker_connected: bool = False
    approval_valid: bool = False
    unresolved_critical_incidents: int = 0
    risk_decision: RiskDecision | None = None


@dataclass(frozen=True)
class PreTradeDecision:
    """Outcome of the pre-trade gate, with a stable, auditable reason set."""

    approved: bool
    order_hash: str
    correlation_id: str
    reason_codes: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    checks_passed: tuple[str, ...] = ()
    checks_failed: tuple[str, ...] = ()
    risk_increasing: bool = True

    def audit_payload(self) -> dict:
        """Immutable audit record for the AuditLog (spec step 10)."""
        return {
            "order_hash": self.order_hash,
            "correlation_id": self.correlation_id,
            "approved": self.approved,
            "risk_increasing": self.risk_increasing,
            "reason_codes": list(self.reason_codes),
            "details": list(self.details),
            "checks_passed": list(self.checks_passed),
            "checks_failed": list(self.checks_failed),
        }


class PreTradeValidator:
    """Runs the ordered pre-trade gate sequence for one order."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or RealClock()

    def validate(self, ctx: PreTradeContext) -> PreTradeDecision:
        order = ctx.order
        config = ctx.config
        order_hash = compute_order_hash(order, config_version=config.config_version)
        correlation_id = order.correlation_id or order.intent_id or order.client_order_id
        risk_increasing = order.side is OrderSide.BUY

        passed: list[str] = []
        failed: list[str] = []
        reasons: list[PreTradeReason] = []
        details: list[str] = []

        def fail(reason: PreTradeReason, detail: str) -> None:
            if reason not in reasons:
                reasons.append(reason)
                failed.append(reason.name)
                details.append(detail)

        def ok(name: str) -> None:
            passed.append(name)

        # 1. Request schema.
        schema_errors = self._schema_errors(order)
        if schema_errors:
            for err in schema_errors:
                fail(err[0], err[1])
        else:
            ok("SCHEMA_VALID")

        # 1b. Strategy / playbook eligibility.
        if not ctx.strategy_live_allowed:
            fail(PreTradeReason.STRATEGY_NOT_LIVE_ALLOWED, "Strategy/playbook is not eligible for live.")
        else:
            ok("STRATEGY_LIVE_ALLOWED")

        # 1c. Upstream deterministic risk decision, if supplied.
        if ctx.risk_decision is not None:
            rd = ctx.risk_decision
            if not rd.approved or rd.verdict is not RiskVerdict.APPROVED:
                fail(
                    PreTradeReason.RISK_ENGINE_REJECTED,
                    f"Risk decision verdict is {rd.verdict.value}.",
                )
            elif to_decimal(order.qty) > to_decimal(rd.approved_qty):
                fail(
                    PreTradeReason.RISK_ENGINE_REJECTED,
                    f"Order qty {order.qty} exceeds risk-approved qty {rd.approved_qty}.",
                )
            else:
                ok("RISK_DECISION_APPROVED")

        # 2. Section / session / symbol eligibility.
        if config.allowed_section is not Section.STOCKS:
            fail(PreTradeReason.SECTION_MISMATCH, "Live configuration is not scoped to STOCKS.")
        elif not ctx.session_open:
            fail(PreTradeReason.SESSION_CLOSED, "Market session is closed.")
        elif order.symbol not in config.allowed_symbols:
            fail(
                PreTradeReason.SYMBOL_NOT_IN_UNIVERSE,
                f"Symbol {order.symbol!r} is not in the approved universe.",
            )
        else:
            ok("SESSION_AND_SYMBOL_ELIGIBLE")

        # 3. Market-data freshness.
        price = self._reference_price(order, ctx)
        if ctx.market is None or price is None or price <= 0:
            fail(PreTradeReason.PRICE_INVALID, "No usable reference price / market snapshot.")
        elif ctx.market.as_of > ctx.now:
            fail(PreTradeReason.STALE_MARKET_DATA, "Market snapshot is from the future.")
        elif (
            ctx.market_data_max_age_seconds is not None
            and (ctx.now - ctx.market.as_of).total_seconds() > ctx.market_data_max_age_seconds
        ):
            fail(
                PreTradeReason.STALE_MARKET_DATA,
                f"Market data is older than {ctx.market_data_max_age_seconds}s.",
            )
        else:
            ok("MARKET_DATA_FRESH")

        # 4/5. Exposure, concentration, frequency, daily loss (risk-increasing).
        if risk_increasing and price is not None and price > 0:
            order_notional = to_decimal(order.qty) * price
            if order_notional > config.max_order_notional:
                fail(
                    PreTradeReason.MAX_ORDER_NOTIONAL,
                    f"Order notional {order_notional} exceeds cap {config.max_order_notional}.",
                )
            position_notional = self._position_notional(ctx.open_positions, order.symbol)
            if position_notional + order_notional > config.max_position_notional:
                fail(
                    PreTradeReason.MAX_POSITION_NOTIONAL,
                    f"Resulting position {position_notional + order_notional} exceeds cap "
                    f"{config.max_position_notional}.",
                )
            total = sum((p.notional for p in ctx.open_positions), Decimal(0)) + order_notional
            if total > config.max_live_capital:
                fail(
                    PreTradeReason.MAX_TOTAL_EXPOSURE,
                    f"Total exposure {total} exceeds live capital {config.max_live_capital}.",
                )
            if (
                position_notional == 0
                and len({p.symbol for p in ctx.open_positions}) >= config.max_concurrent_positions
            ):
                fail(
                    PreTradeReason.MAX_CONCURRENT_POSITIONS,
                    f"Concurrent positions cap {config.max_concurrent_positions} reached.",
                )
            # Daily loss / drawdown.
            daily_pnl = to_decimal(ctx.account.realized_pnl_today) + to_decimal(
                ctx.account.unrealized_pnl_today
            )
            if daily_pnl <= -config.max_daily_loss:
                fail(
                    PreTradeReason.DAILY_LOSS,
                    f"Daily P&L {daily_pnl} breaches loss limit {-config.max_daily_loss}.",
                )
            if (
                ctx.account.start_of_day_equity > 0
                and to_decimal(ctx.account.total_equity)
                < to_decimal(ctx.account.start_of_day_equity) - config.max_daily_loss
            ):
                fail(PreTradeReason.DRAWDOWN, "Equity drawdown breaches the daily loss limit.")
            if not reasons:
                ok("EXPOSURE_AND_LOSS_WITHIN_LIMITS")

        # Order frequency (always enforced).
        window_start = ctx.now.timestamp() - config.order_frequency_window_seconds
        recent = [t for t in ctx.recent_order_times if t.timestamp() >= window_start]
        if len(recent) >= config.max_order_frequency:
            fail(
                PreTradeReason.ORDER_FREQUENCY,
                f"{len(recent)} orders in the last {config.order_frequency_window_seconds}s "
                f"reaches the cap {config.max_order_frequency}.",
            )
        else:
            ok("ORDER_FREQUENCY_WITHIN_LIMITS")

        # 6. Duplicate / stale / conflicting intent.
        if order_hash in ctx.recent_order_hashes:
            fail(PreTradeReason.DUPLICATE_ORDER, "An identical order intent was already submitted.")
        elif (
            ctx.intent_max_age_seconds is not None
            and (ctx.now - order.created_at).total_seconds() > ctx.intent_max_age_seconds
        ):
            fail(PreTradeReason.STALE_INTENT, "Order intent is older than the permitted age.")
        else:
            ok("INTENT_UNIQUE_AND_FRESH")

        # 7. Kill switch (blocks new risk only).
        if ctx.kill_switch_active and risk_increasing:
            fail(PreTradeReason.HALT_ACTIVE, "Kill switch is active; new risk-increasing orders are blocked.")
        else:
            ok("KILL_SWITCH_CLEAR_FOR_SCOPE")

        # 8. Watchdog health.
        if not ctx.watchdog_healthy:
            fail(PreTradeReason.WATCHDOG_UNHEALTHY, "Watchdog is not healthy.")
        else:
            ok("WATCHDOG_HEALTHY")

        # Broker connectivity.
        if not ctx.broker_connected:
            fail(PreTradeReason.BROKER_DISCONNECTED, "Broker connectivity check has not passed.")
        else:
            ok("BROKER_CONNECTED")

        # Unresolved critical incidents block new risk.
        if ctx.unresolved_critical_incidents > 0 and risk_increasing:
            fail(
                PreTradeReason.UNRESOLVED_INCIDENT,
                f"{ctx.unresolved_critical_incidents} unresolved critical incident(s).",
            )
        else:
            ok("NO_BLOCKING_INCIDENTS")

        # 9. Authenticated manual approval (when required).
        if config.manual_approval_required and not ctx.approval_valid:
            fail(PreTradeReason.APPROVAL_INVALID, "A valid, unconsumed manual approval is required.")
        else:
            ok("APPROVAL_VALID")

        # 10. Buying power (risk-increasing buys only).
        if risk_increasing and price is not None and price > 0:
            if to_decimal(order.qty) * price > to_decimal(ctx.account.buying_power):
                fail(PreTradeReason.BUYING_POWER, "Order notional exceeds available buying power.")
            else:
                ok("BUYING_POWER_SUFFICIENT")

        return PreTradeDecision(
            approved=not reasons,
            order_hash=order_hash,
            correlation_id=correlation_id,
            reason_codes=tuple(r.value for r in reasons),
            details=tuple(details),
            checks_passed=tuple(passed),
            checks_failed=tuple(failed),
            risk_increasing=risk_increasing,
        )

    # ── Helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _schema_errors(order: Order) -> list[tuple[PreTradeReason, str]]:
        errors: list[tuple[PreTradeReason, str]] = []
        if not order.symbol.strip():
            errors.append((PreTradeReason.SCHEMA_INVALID, "symbol is empty."))
        if order.qty <= 0:
            errors.append((PreTradeReason.QTY_INVALID, "qty must be > 0."))
        if order.type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and order.limit_price is None:
            errors.append((PreTradeReason.SCHEMA_INVALID, "limit order requires a limit_price."))
        if order.type in (OrderType.STOP, OrderType.STOP_LIMIT) and order.stop_price is None:
            errors.append((PreTradeReason.SCHEMA_INVALID, "stop order requires a stop_price."))
        if order.type not in (OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT):
            errors.append((PreTradeReason.ORDER_TYPE_INVALID, f"unsupported order type {order.type}."))
        return errors

    @staticmethod
    def _position_notional(positions: tuple[OpenPosition, ...], symbol: str) -> Decimal:
        return sum((p.notional for p in positions if p.symbol == symbol), Decimal(0))

    @staticmethod
    def _reference_price(order: Order, ctx: PreTradeContext) -> Decimal | None:
        if order.limit_price is not None:
            return to_decimal(order.limit_price)
        if order.stop_price is not None:
            return to_decimal(order.stop_price)
        if ctx.market is not None:
            return to_decimal(ctx.market.last_price)
        return None
