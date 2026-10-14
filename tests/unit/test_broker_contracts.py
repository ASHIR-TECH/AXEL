"""Phase 6 broker adapter contract tests (spec §9)."""

from decimal import Decimal

import pytest

from axel.core.contracts import Order, RiskDecision
from axel.core.types import OrderSide, OrderState, OrderType, RiskVerdict
from axel.execution.alpaca import AlpacaAdapter, normalize_state
from axel.execution.broker_base import (
    AmbiguousSubmissionError,
    BrokerRejectedError,
    NonRetryableBrokerError,
    RetryableBrokerError,
    parse_decimal,
    sanitize_payload,
)


def _order(**overrides) -> Order:
    base = {
        "client_order_id": "ord_contract_1",
        "proposal_id": "prop_1",
        "symbol": "SPY",
        "side": OrderSide.BUY,
        "qty": 10.0,
        "type": OrderType.LIMIT,
        "limit_price": 100.0,
        "strategy_id": "strat_v1",
        "strategy_version": "1.0.0",
    }
    base.update(overrides)
    return Order(**base)


def _decision(approved: bool = True, verdict: RiskVerdict = RiskVerdict.APPROVED) -> RiskDecision:
    return RiskDecision(
        proposal_id="prop_1",
        approved=approved,
        verdict=verdict,
        approved_qty=10.0,
        approved_notional_usd=1000.0,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("new", OrderState.ACKNOWLEDGED),
        ("accepted", OrderState.ACKNOWLEDGED),
        ("partially_filled", OrderState.PARTIALLY_FILLED),
        ("filled", OrderState.FILLED),
        ("canceled", OrderState.CANCELED),
        ("expired", OrderState.EXPIRED),
        ("rejected", OrderState.REJECTED),
        ("pending_cancel", OrderState.CANCEL_PENDING),
        ("something_new", OrderState.UNKNOWN),
        (None, OrderState.UNKNOWN),
    ],
)
def test_state_normalization(status, expected):
    assert normalize_state(status) is expected


def test_submit_requires_approved_risk_decision():
    broker = AlpacaAdapter(mock_mode=True)
    with pytest.raises(PermissionError):
        broker.submit_order(_order(), _decision(approved=False, verdict=RiskVerdict.REJECTED))


def test_submit_rejects_non_positive_qty():
    broker = AlpacaAdapter(mock_mode=True)
    with pytest.raises(ValueError):
        broker.submit_order(_order(qty=0.0), _decision())


def test_mock_submit_is_idempotent_by_client_id():
    broker = AlpacaAdapter(mock_mode=True)
    first = broker.submit_order(_order(), _decision())
    second = broker.submit_order(_order(), _decision())
    assert first.broker_order_id == second.broker_order_id
    assert second.state is OrderState.ACKNOWLEDGED
    assert len(broker.get_open_orders()) == 1


def test_ambiguous_submit_raises_and_does_not_pretend_success():
    broker = AlpacaAdapter(mock_mode=True)
    broker.fail_next_submit = "ambiguous_unsent"
    with pytest.raises(AmbiguousSubmissionError):
        broker.submit_order(_order(), _decision())
    assert broker.find_order_by_client_id("ord_contract_1") is None


def test_ambiguous_sent_records_order_that_reconciliation_can_find():
    broker = AlpacaAdapter(mock_mode=True)
    broker.fail_next_submit = "ambiguous_sent"
    with pytest.raises(AmbiguousSubmissionError):
        broker.submit_order(_order(), _decision())
    # The order did reach the venue; lookup must resolve it.
    assert broker.find_order_by_client_id("ord_contract_1") is not None


@pytest.mark.parametrize(
    ("fault", "exc"),
    [
        ("transport", RetryableBrokerError),
        ("rejected", BrokerRejectedError),
        ("nonretryable", NonRetryableBrokerError),
    ],
)
def test_submit_error_taxonomy(fault, exc):
    broker = AlpacaAdapter(mock_mode=True)
    broker.fail_next_submit = fault
    with pytest.raises(exc):
        broker.submit_order(_order(), _decision())


def test_fault_injection_is_single_shot():
    broker = AlpacaAdapter(mock_mode=True)
    broker.fail_next_submit = "transport"
    with pytest.raises(RetryableBrokerError):
        broker.submit_order(_order(), _decision())
    # Second call succeeds without re-injecting the fault.
    assert broker.submit_order(_order(), _decision()).broker_order_id


def test_account_uses_decimal_and_lookup_methods():
    broker = AlpacaAdapter(mock_mode=True)
    account = broker.get_account()
    assert isinstance(account.total_equity, Decimal)
    assert account.account_id == "mock-account"

    result = broker.submit_order(_order(), _decision())
    by_id = broker.get_order(result.broker_order_id)
    by_client = broker.find_order_by_client_id("ord_contract_1")
    assert by_id is not None and by_client is not None
    assert by_id.client_order_id == by_client.client_order_id == "ord_contract_1"


def test_lookup_missing_returns_none():
    broker = AlpacaAdapter(mock_mode=True)
    assert broker.get_order("nope") is None
    assert broker.find_order_by_client_id("nope") is None


def test_cancel_and_cancel_all():
    broker = AlpacaAdapter(mock_mode=True)
    broker.submit_order(_order(client_order_id="a"), _decision())
    broker.submit_order(_order(client_order_id="b"), _decision())

    result = broker.cancel("a")
    assert result.accepted and result.state is OrderState.CANCELED
    assert broker.find_order_by_client_id("a").state is OrderState.CANCELED

    assert broker.cancel_all_orders() == 1
    assert broker.cancel("missing").accepted is False


def test_simulate_fill_updates_order_and_positions():
    broker = AlpacaAdapter(mock_mode=True)
    broker.submit_order(_order(), _decision())
    broker.simulate_fill("ord_contract_1", qty=10, price=101, fee="0.5", partial=False)

    order = broker.find_order_by_client_id("ord_contract_1")
    assert order.state is OrderState.FILLED
    assert order.filled_qty == Decimal(10)
    assert order.filled_avg_price == Decimal(101)

    fills = broker.list_fills()
    assert len(fills) == 1
    assert fills[0].fee == Decimal("0.5")

    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "SPY"
    assert positions[0].qty == 10.0


def test_health_check_reports_state():
    broker = AlpacaAdapter(mock_mode=True)
    assert broker.health_check().healthy is True
    broker.health_ok = False
    health = broker.health_check()
    assert health.healthy is False
    assert health.detail


@pytest.mark.parametrize(
    "configured, expected",
    [
        ("https://paper-api.alpaca.markets", "https://paper-api.alpaca.markets"),
        ("https://paper-api.alpaca.markets/", "https://paper-api.alpaca.markets"),
        ("https://paper-api.alpaca.markets/v2", "https://paper-api.alpaca.markets"),
        ("https://paper-api.alpaca.markets/v2/", "https://paper-api.alpaca.markets"),
    ],
)
def test_base_url_normalised_to_host_root(configured, expected):
    # A configured "/v2" must not produce a double "/v2" in request paths.
    broker = AlpacaAdapter(base_url=configured, mock_mode=True)
    assert broker.base_url == expected
    assert f"{broker.base_url}/v2/account" == f"{expected}/v2/account"


def test_sanitize_payload_redacts_secrets_and_sensitive_keys():
    payload = {
        "symbol": "SPY",
        "api_key": "AK123",
        "nested": {"APCA-API-SECRET-KEY": "shhh", "token": "t", "ok": "keep"},
        "note": "super-secret",
    }
    clean = sanitize_payload(payload, secrets=("super-secret",))
    assert clean["api_key"] == "***REDACTED***"
    assert clean["nested"]["APCA-API-SECRET-KEY"] == "***REDACTED***"
    assert clean["nested"]["token"] == "***REDACTED***"
    assert clean["nested"]["ok"] == "keep"
    assert clean["note"] == "***REDACTED***"
    assert clean["symbol"] == "SPY"
    # Original payload is untouched.
    assert payload["api_key"] == "AK123"


def test_parse_decimal_rejects_garbage():
    assert parse_decimal("10.5", field_name="qty") == Decimal("10.5")
    with pytest.raises(NonRetryableBrokerError):
        parse_decimal("not-a-number", field_name="qty")
    with pytest.raises(NonRetryableBrokerError):
        parse_decimal(None, field_name="qty")


def test_provider_event_time_preserved_separately_from_receive_time():
    broker = AlpacaAdapter(mock_mode=True)
    broker.submit_order(_order(), _decision())
    fill = broker.simulate_fill("ord_contract_1", qty=5, price=100, partial=True)
    assert fill.event_time is not None
    assert fill.received_at is not None
    assert fill.event_time.tzinfo is not None
