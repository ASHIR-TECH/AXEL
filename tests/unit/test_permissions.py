"""Phase 6 permission and fail-closed tests (spec §14 / §15.4)."""

from datetime import UTC, datetime

import pytest

from axel.core.config import settings
from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, OrderState, OrderType, RiskVerdict
from axel.execution.alpaca import AlpacaAdapter
from axel.execution.approval import AuthenticatedPrincipal, PrincipalKind
from axel.execution.broker_base import sanitize_payload
from axel.execution.live_gate import LiveGate
from axel.execution.order_service import OrderService
from axel.execution.order_store import OrderEventKind, OrderEventStore
from axel.execution.permissions import assert_may_cancel, assert_may_submit
from axel.execution.reason_codes import HaltReason
from axel.risk.halt import HaltController

NOW = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)

AGENT = AuthenticatedPrincipal("agent-1", PrincipalKind.AGENT, "auth-agent")
HUMAN = AuthenticatedPrincipal("ops-1", PrincipalKind.HUMAN, "auth-human")
SERVICE = AuthenticatedPrincipal("exec-svc", PrincipalKind.SERVICE, "auth-svc")


class CountingAlpaca(AlpacaAdapter):
    def __init__(self, **kwargs):
        super().__init__(mock_mode=True, **kwargs)
        self.submit_calls = 0

    def _execute_order(self, order, risk_decision):
        self.submit_calls += 1
        return super()._execute_order(order, risk_decision)


def _order() -> Order:
    return Order(
        client_order_id="ord-perm",
        proposal_id="prop-perm",
        symbol="SPY",
        side=OrderSide.BUY,
        qty=10.0,
        type=OrderType.LIMIT,
        limit_price=100.0,
        strategy_id="s1",
        strategy_version="1.0.0",
    )


def _decision() -> RiskDecision:
    return RiskDecision(
        proposal_id="prop-perm", approved=True, verdict=RiskVerdict.APPROVED, approved_qty=10.0
    )


def test_agent_principal_cannot_submit_orders():
    broker = CountingAlpaca()
    service = OrderService(broker)
    with pytest.raises(PermissionError):
        service.submit(_order(), _decision(), config_version="cfg-1", principal=AGENT)
    assert broker.submit_calls == 0


def test_agent_principal_cannot_cancel_orders():
    broker = CountingAlpaca()
    service = OrderService(broker)
    service.submit(_order(), _decision(), config_version="cfg-1", principal=SERVICE)

    with pytest.raises(PermissionError):
        service.cancel("ord-perm", principal=AGENT)
    assert service.store.record("ord-perm").state is OrderState.ACKNOWLEDGED


def test_human_and_service_may_submit_and_cancel():
    broker = CountingAlpaca()
    service = OrderService(broker)
    outcome = service.submit(_order(), _decision(), config_version="cfg-1", principal=HUMAN)
    assert outcome.record.state is OrderState.ACKNOWLEDGED
    record = service.cancel("ord-perm", principal=SERVICE)
    assert record.state is OrderState.CANCELED


def test_guard_helpers_reject_agents_directly():
    with pytest.raises(PermissionError):
        assert_may_submit(AGENT)
    with pytest.raises(PermissionError):
        assert_may_cancel(AGENT)
    # Non-agent kinds and the internal (None) path are permitted.
    assert_may_submit(HUMAN)
    assert_may_submit(SERVICE)
    assert_may_submit(None)
    assert_may_cancel(None)


def test_internal_execution_path_without_principal_still_works():
    broker = CountingAlpaca()
    service = OrderService(broker)
    outcome = service.submit(_order(), _decision(), config_version="cfg-1")
    assert outcome.record.state is OrderState.ACKNOWLEDGED


def test_live_gate_disabled_by_default_without_config():
    gate = LiveGate()
    assert gate.is_configured is False
    assert gate.config is None
    assert gate.validate().enabled is False


def test_adapter_defaults_to_mock_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "alpaca_api_key", None)
    monkeypatch.setattr(settings, "alpaca_secret_key", None)
    broker = AlpacaAdapter()
    assert broker.mock_mode is True


def test_environment_defaults_to_paper_and_not_live_confirmed():
    assert settings.environment == "paper"
    assert settings.live_trading_confirmed is False


def test_broker_never_leaks_secrets_in_errors():
    redacted = sanitize_payload(
        {"api_key": "SECRET-KEY", "secret": "SECRET-VALUE", "symbol": "SPY"},
        secrets=("SECRET-VALUE",),
    )
    assert "SECRET-KEY" not in str(redacted)
    assert "SECRET-VALUE" not in str(redacted)
    assert redacted["symbol"] == "SPY"


def test_watchdog_can_halt_without_any_agent_or_llm(tmp_path):
    # The halt path is deterministic and must work with no LLM/agent available.
    halt = HaltController(state_file_path=tmp_path / "halt.json")
    halt.evaluate_broker_health(False, source="watchdog")
    assert halt.is_halted
    assert HaltReason.BROKER_OUTAGE.value in halt.state().reason_codes


def test_order_store_never_persists_credentials():
    store = OrderEventStore()
    store.append(
        client_order_id="o",
        kind=OrderEventKind.CREATED,
        state=OrderState.CREATED,
        metadata={"symbol": "SPY", "side": "buy"},
    )
    dumped = str(store.snapshot())
    assert "secret" not in dumped.lower()
    assert "api_key" not in dumped.lower()
