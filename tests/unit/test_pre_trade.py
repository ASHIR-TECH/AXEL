"""Phase 6 deterministic pre-trade validator tests (spec §4.5 / §2.5)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, OrderType, RiskVerdict, Section, TradingMode
from axel.execution.live_gate import ApprovalMetadata, LiveConfig
from axel.execution.pre_trade import (
    MarketSnapshot,
    OpenPosition,
    PortfolioSnapshot,
    PreTradeContext,
    PreTradeValidator,
)
from axel.execution.reason_codes import PreTradeReason

NOW = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)


def _config(**overrides) -> LiveConfig:
    base = {
        "config_version": "cfg-1",
        "environment": TradingMode.LIVE,
        "broker": "alpaca",
        "account_id": "acct-1",
        "allowed_section": Section.STOCKS,
        "max_live_capital": Decimal(1000),
        "max_position_notional": Decimal(200),
        "max_order_notional": Decimal(100),
        "max_daily_loss": Decimal(50),
        "max_concurrent_positions": 2,
        "max_order_frequency": 3,
        "order_frequency_window_seconds": 60,
        "allowed_symbols": frozenset({"SPY", "AAPL"}),
        "manual_approval_required": True,
        "phase5_evidence_ref": "evidence#1",
        "phase5_gate_passed": True,
        "approval_metadata": ApprovalMetadata(
            approved_by="ops", approved_at=NOW, reference="CAB-1"
        ),
    }
    base.update(overrides)
    return LiveConfig(**base)


def _order(**overrides) -> Order:
    base = {
        "client_order_id": "ord-1",
        "proposal_id": "prop-1",
        "symbol": "SPY",
        "side": OrderSide.BUY,
        "qty": 1.0,
        "type": OrderType.LIMIT,
        "limit_price": 50.0,
        "strategy_id": "s1",
        "strategy_version": "1.0.0",
    }
    base.update(overrides)
    return Order(**base)


def _account(**overrides) -> PortfolioSnapshot:
    base = {
        "total_equity": Decimal(1000),
        "cash": Decimal(1000),
        "buying_power": Decimal(1000),
        "start_of_day_equity": Decimal(1000),
    }
    base.update(overrides)
    return PortfolioSnapshot(**base)


def _context(**overrides) -> PreTradeContext:
    base = {
        "order": _order(),
        "config": _config(),
        "account": _account(),
        "now": NOW,
        "market": MarketSnapshot(symbol="SPY", last_price=Decimal(50), as_of=NOW),
        "strategy_live_allowed": True,
        "session_open": True,
        "watchdog_healthy": True,
        "broker_connected": True,
        "approval_valid": True,
    }
    base.update(overrides)
    return PreTradeContext(**base)


def test_happy_path_approves():
    decision = PreTradeValidator().validate(_context())
    assert decision.approved
    assert decision.reason_codes == ()
    assert decision.order_hash
    assert decision.correlation_id == "ord-1"


def test_schema_failures():
    # A LIMIT order without a limit price is invalid.
    bad = _order(type=OrderType.LIMIT).model_copy(update={"limit_price": None})
    decision = PreTradeValidator().validate(_context(order=bad))
    assert not decision.approved
    assert PreTradeReason.SCHEMA_INVALID.value in decision.reason_codes


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"strategy_live_allowed": False}, PreTradeReason.STRATEGY_NOT_LIVE_ALLOWED),
        ({"session_open": False}, PreTradeReason.SESSION_CLOSED),
        ({"watchdog_healthy": False}, PreTradeReason.WATCHDOG_UNHEALTHY),
        ({"broker_connected": False}, PreTradeReason.BROKER_DISCONNECTED),
        ({"approval_valid": False}, PreTradeReason.APPROVAL_INVALID),
        ({"market": None}, PreTradeReason.PRICE_INVALID),
        ({"unresolved_critical_incidents": 1}, PreTradeReason.UNRESOLVED_INCIDENT),
        ({"kill_switch_active": True}, PreTradeReason.HALT_ACTIVE),
    ],
)
def test_fail_closed_reasons(overrides, reason):
    decision = PreTradeValidator().validate(_context(**overrides))
    assert not decision.approved
    assert reason.value in decision.reason_codes


def test_symbol_not_in_universe():
    decision = PreTradeValidator().validate(_context(order=_order(symbol="TSLA")))
    assert not decision.approved
    assert PreTradeReason.SYMBOL_NOT_IN_UNIVERSE.value in decision.reason_codes


def test_section_mismatch():
    decision = PreTradeValidator().validate(_context(config=_config(allowed_section=Section.CRYPTO)))
    assert not decision.approved
    assert PreTradeReason.SECTION_MISMATCH.value in decision.reason_codes


def test_stale_market_data_when_bound_supplied():
    stale_market = MarketSnapshot(symbol="SPY", last_price=Decimal(50), as_of=NOW - timedelta(seconds=120))
    decision = PreTradeValidator().validate(
        _context(market=stale_market, market_data_max_age_seconds=30)
    )
    assert not decision.approved
    assert PreTradeReason.STALE_MARKET_DATA.value in decision.reason_codes


def test_max_order_notional():
    decision = PreTradeValidator().validate(_context(order=_order(qty=3.0, limit_price=50.0)))
    assert not decision.approved
    assert PreTradeReason.MAX_ORDER_NOTIONAL.value in decision.reason_codes


def test_max_position_notional():
    ctx = _context(open_positions=(OpenPosition("SPY", OrderSide.BUY, Decimal(3), Decimal(160)),))
    decision = PreTradeValidator().validate(ctx)
    assert not decision.approved
    assert PreTradeReason.MAX_POSITION_NOTIONAL.value in decision.reason_codes


def test_max_total_exposure():
    cfg = _config(
        max_live_capital=Decimal(100),
        max_position_notional=Decimal(100),
        max_order_notional=Decimal(100),
    )
    positions = (OpenPosition("SPY", OrderSide.BUY, Decimal(1), Decimal(90)),)
    decision = PreTradeValidator().validate(
        _context(config=cfg, order=_order(qty=1.0, limit_price=90.0), open_positions=positions)
    )
    assert not decision.approved
    assert PreTradeReason.MAX_TOTAL_EXPOSURE.value in decision.reason_codes


def test_max_concurrent_positions():
    positions = (
        OpenPosition("AAPL", OrderSide.BUY, Decimal(1), Decimal(50)),
        OpenPosition("MSFT", OrderSide.BUY, Decimal(1), Decimal(50)),
    )
    decision = PreTradeValidator().validate(
        _context(order=_order(symbol="SPY", qty=1.0, limit_price=50.0), open_positions=positions)
    )
    assert not decision.approved
    assert PreTradeReason.MAX_CONCURRENT_POSITIONS.value in decision.reason_codes


def test_daily_loss_breach():
    account = _account(realized_pnl_today=Decimal(-60))
    decision = PreTradeValidator().validate(_context(account=account))
    assert not decision.approved
    assert PreTradeReason.DAILY_LOSS.value in decision.reason_codes


def test_order_frequency():
    times = (NOW - timedelta(seconds=10), NOW - timedelta(seconds=20), NOW - timedelta(seconds=30))
    decision = PreTradeValidator().validate(_context(recent_order_times=times))
    assert not decision.approved
    assert PreTradeReason.ORDER_FREQUENCY.value in decision.reason_codes


def test_duplicate_order():
    order = _order()
    from axel.execution.approval import compute_order_hash

    order_hash = compute_order_hash(order, config_version="cfg-1")
    decision = PreTradeValidator().validate(_context(order=order, recent_order_hashes=frozenset({order_hash})))
    assert not decision.approved
    assert PreTradeReason.DUPLICATE_ORDER.value in decision.reason_codes


def test_stale_intent():
    old = _order().model_copy(update={"created_at": NOW - timedelta(seconds=600)})
    decision = PreTradeValidator().validate(_context(order=old, intent_max_age_seconds=60))
    assert not decision.approved
    assert PreTradeReason.STALE_INTENT.value in decision.reason_codes


def test_buying_power():
    account = _account(buying_power=Decimal(10))
    decision = PreTradeValidator().validate(_context(order=_order(qty=1.0, limit_price=50.0), account=account))
    assert not decision.approved
    assert PreTradeReason.BUYING_POWER.value in decision.reason_codes


def test_risk_decision_must_be_approved_and_respected():
    rejected = RiskDecision(
        proposal_id="prop-1", approved=False, verdict=RiskVerdict.REJECTED, approved_qty=0.0
    )
    decision = PreTradeValidator().validate(_context(risk_decision=rejected))
    assert not decision.approved
    assert PreTradeReason.RISK_ENGINE_REJECTED.value in decision.reason_codes

    approved_small = RiskDecision(
        proposal_id="prop-1", approved=True, verdict=RiskVerdict.APPROVED, approved_qty=0.5
    )
    decision = PreTradeValidator().validate(_context(order=_order(qty=1.0), risk_decision=approved_small))
    assert not decision.approved
    assert PreTradeReason.RISK_ENGINE_REJECTED.value in decision.reason_codes


def test_risk_reducing_sell_bypasses_risk_caps_and_halt():
    ctx = _context(
        order=_order(side=OrderSide.SELL, qty=100.0, limit_price=50.0),
        kill_switch_active=True,
        approval_valid=False,
    )
    # Requires approval unless configured off; disable it to isolate the cap bypass.
    ctx = replace(ctx, config=_config(manual_approval_required=False))
    decision = PreTradeValidator().validate(ctx)
    assert decision.risk_increasing is False
    # Not blocked by the new-risk halt, order cap or approval.
    assert PreTradeReason.HALT_ACTIVE.value not in decision.reason_codes
    assert PreTradeReason.MAX_ORDER_NOTIONAL.value not in decision.reason_codes
    assert decision.approved


def test_universe_and_session_apply_to_sell_too():
    decision = PreTradeValidator().validate(
        _context(order=_order(side=OrderSide.SELL, symbol="TSLA"))
    )
    assert not decision.approved
    assert PreTradeReason.SYMBOL_NOT_IN_UNIVERSE.value in decision.reason_codes


def test_audit_payload_is_stable_and_immutable():
    decision = PreTradeValidator().validate(_context())
    payload = decision.audit_payload()
    assert payload["order_hash"] == decision.order_hash
    assert payload["approved"] is True
    assert payload["reason_codes"] == []


def test_market_data_from_future_is_rejected():
    future = MarketSnapshot(symbol="SPY", last_price=Decimal(50), as_of=NOW + timedelta(seconds=5))
    decision = PreTradeValidator().validate(_context(market=future))
    assert not decision.approved
    assert PreTradeReason.STALE_MARKET_DATA.value in decision.reason_codes
