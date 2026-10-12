# Phase 6 — How to run

This guide is for developers and operators working on the Phase 6 tiny-live
Stocks execution path. It covers environment setup, the test suite, the module
map, and copy-pasteable usage of every new Phase 6 component.

The controlling specification is
[`AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md`](./AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md).
Operational procedures live in [`phase-6-runbook.md`](./phase-6-runbook.md);
the staged go-live plan lives in [`phase-6-rollout.md`](./phase-6-rollout.md).

> **Live trading is disabled by default.** Nothing in this repository enables
> live trading in CI, development or the default deployment. Live orders require
> a validated `LiveConfig`, a human approval and a passing pre-trade gate.

---

## 1. Environment

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Configuration is read from `.env` (Pydantic settings in `axel/core/config.py`).
Safe defaults: `environment=paper`, `live_trading_confirmed=false`,
`alpaca_paper=true`. Do not commit `.env` or any API key/secret.

---

## 2. Checks (run before every commit)

```bash
# Full test suite
.venv/bin/python -m pytest -q

# Lint
.venv/bin/python -m ruff check .

# Architectural boundary: zero LLM imports in risk/ and execution/
.venv/bin/python scripts/check_no_llm_imports.py
```

Targeted Phase 6 suites:

```bash
.venv/bin/python -m pytest -q \
  tests/unit/test_live_gate.py \
  tests/unit/test_order_approval.py \
  tests/unit/test_order_fsm_phase6.py \
  tests/unit/test_broker_contracts.py \
  tests/unit/test_order_store.py \
  tests/unit/test_order_service.py \
  tests/unit/test_pre_trade.py \
  tests/unit/test_incidents.py \
  tests/unit/test_reconcile_phase6.py \
  tests/unit/test_halt.py \
  tests/unit/test_analytics.py \
  tests/unit/test_metrics_phase6.py \
  tests/unit/test_failure_injection.py \
  tests/unit/test_permissions.py \
  tests/property/test_order_fsm_properties.py \
  tests/integration/test_phase6_db_models.py
```

---

## 3. Module map

| Module | Responsibility |
| --- | --- |
| `axel/core/money.py` | Fixed-point money helpers (`to_decimal`) |
| `axel/core/ids.py` | `derive_client_order_id(order_hash)` — deterministic, ≤128 chars |
| `axel/execution/reason_codes.py` | Stable machine-readable reason/severity/status enums |
| `axel/execution/live_gate.py` | Fail-closed, versioned live activation gate |
| `axel/execution/approval.py` | Order-hash-bound, single-use, authenticated approvals |
| `axel/execution/broker_base.py` | Canonical broker adapter contract, records, error taxonomy |
| `axel/execution/alpaca.py` | Alpaca adapter (mock + real) with fault injection |
| `axel/execution/order_fsm.py` | Canonical order state machine |
| `axel/execution/order_store.py` | Append-only order event log + replayed projection |
| `axel/execution/order_service.py` | Idempotent submit / recovery / fill / cancel service |
| `axel/execution/permissions.py` | Execution-authority guards (agents may not submit/cancel) |
| `axel/execution/pre_trade.py` | Deterministic pre-trade validator |
| `axel/execution/incidents.py` | Deterministic incident model and store |
| `axel/execution/reconcile.py` | Full reconciliation loop and mismatch taxonomy |
| `axel/execution/analytics.py` | Versioned execution/cost metrics |
| `axel/risk/halt.py` | Scoped, persistent, DB-independent halt controller |
| `axel/dashboard/metrics.py` | Phase 6 operational Prometheus fields |

---

## 4. Using the components

### 4.1 Live gate (fail-closed)

```python
from datetime import UTC, datetime
from decimal import Decimal

from axel.core.types import Section, TradingMode
from axel.execution.live_gate import ApprovalMetadata, LiveConfig, LiveGate

config = LiveConfig(
    config_version="live-2026-06-01",
    environment=TradingMode.LIVE,
    broker="alpaca",
    account_id="acct-123",
    allowed_section=Section.STOCKS,
    max_live_capital=Decimal("1000"),
    max_position_notional=Decimal("500"),
    max_order_notional=Decimal("500"),
    max_daily_loss=Decimal("50"),
    max_concurrent_positions=5,
    max_order_frequency=10,
    order_frequency_window_seconds=60,
    allowed_symbols=frozenset({"SPY"}),
    manual_approval_required=True,
    phase5_evidence_ref="evidence/paper-soak-2026-05",
    phase5_gate_passed=True,               # must be backed by recorded Phase 5 evidence
    approval_metadata=ApprovalMetadata(
        approved_by="ops-lead", approved_at=datetime.now(UTC), reference="CAB-2026-06-01"
    ),
)

gate = LiveGate(config, max_config_age_seconds=86_400)
decision = gate.evaluate(
    kill_switch_active=False,
    watchdog_healthy=True,
    broker_connected=True,
    observed_account_id="acct-123",
)
assert decision.enabled, decision.reason_codes
```

With no config, `LiveGate().validate().enabled` is `False`. Every prerequisite
that is missing, unknown or unhealthy disables live.

### 4.2 Approval and order submission

```python
from axel.execution.alpaca import AlpacaAdapter
from axel.execution.approval import (
    AuthenticatedPrincipal, OrderApprovalService, PrincipalKind,
)
from axel.execution.order_service import OrderService

broker = AlpacaAdapter()                 # falls back to mock when uncredentialed
service = OrderService(broker)

approvals = OrderApprovalService()
record = approvals.request(order, principal=agent_principal, config_version="live-2026-06-01")
approvals.approve(
    approval_id=record.approval_id,
    approver=AuthenticatedPrincipal("ops-lead", PrincipalKind.HUMAN, "auth-ref"),
    rationale="within limits",
)

outcome = service.submit(
    order,
    risk_decision,
    config_version="live-2026-06-01",
    approvals=approvals,
    principal=service_principal,          # an AGENT principal raises PermissionError
)
print(outcome.record.state, outcome.duplicate, outcome.ambiguous)
```

An ambiguous submit (`outcome.ambiguous`) is **never** blind-retried. Resolve it
by lookup:

```python
recovered = service.resolve_unknown(order.client_order_id)
```

### 4.3 Pre-trade validation

```python
from axel.execution.pre_trade import (
    MarketSnapshot, PortfolioSnapshot, PreTradeContext, PreTradeValidator,
)

decision = PreTradeValidator().validate(
    PreTradeContext(
        order=order,
        config=config,
        account=PortfolioSnapshot(
            total_equity=Decimal("1000"),
            cash=Decimal("1000"),
            buying_power=Decimal("1000"),
            start_of_day_equity=Decimal("1000"),
        ),
        now=datetime.now(UTC),
        market=MarketSnapshot("SPY", Decimal("100"), datetime.now(UTC)),
        strategy_live_allowed=True,
        session_open=True,
        watchdog_healthy=True,
        broker_connected=True,
        approval_valid=True,
    )
)
assert decision.approved, decision.reason_codes
```

Risk-reducing sells bypass risk-increasing caps and the new-risk halt.

### 4.4 Halt controller

```python
from axel.execution.reason_codes import HaltReason, HaltScope
from axel.risk.halt import CancelOutcome, HaltController

halt = HaltController(state_file_path=Path(".axel_halt.json"))
halt.trip(HaltReason.BROKER_OUTAGE, detail="health check failed", source="watchdog")

assert halt.blocks_new_risk(Section.STOCKS)
assert halt.allows_read_only()            # reconciliation still permitted

for client_order_id in halt.plan_cancellations([...]):
    ok = broker.cancel_order(client_order_id)
    halt.record_cancel_outcome(
        client_order_id, CancelOutcome.SUCCEEDED if ok else CancelOutcome.UNKNOWN
    )

halt.recover(
    operator_token=settings.operator_rearm_secret.get_secret_value(),
    written_rationale="broker connectivity verified and books reconciled",
    reconciliation_verified=True,
    blocking_incident_count=0,
)
```

### 4.5 Reconciliation

```python
from axel.execution.incidents import IncidentStore
from axel.execution.reconcile import ReconciliationEngine

engine = ReconciliationEngine(broker, incident_store=IncidentStore())
result = engine.reconcile_live(
    axel_orders=service.store.records(),
    fill_derived_positions=fill_derived_positions,
    axel_cash=axel_cash,
    axel_buying_power=axel_buying_power,
)

if result.blocks_new_risk:
    halt.evaluate_incidents(engine.incidents.critical_open_count(), source="reconciliation")
```

### 4.6 Execution metrics

```python
from axel.execution.analytics import (
    BenchmarkInput, FillInput, OrderAnalyticsInput, compute_execution_metrics,
    summarize_cohort,
)

metrics = compute_execution_metrics(
    OrderAnalyticsInput(
        client_order_id="ord-1",
        symbol="SPY",
        side=OrderSide.BUY,
        requested_qty=Decimal("100"),
        decision_price=Decimal("100"),
        fills=(FillInput(qty=Decimal("100"), price=Decimal("101.6"), fee=Decimal("1.00")),),
        benchmark=BenchmarkInput(
            vwap=Decimal("101"),
            window_start=window_start,
            window_end=window_end,
            source="consolidated_tape",
            print_count=1234,
        ),
    )
)
# Sign convention: s=+1 buy, s=-1 sell; positive means worse execution.
print(metrics.implementation_shortfall_per_share)  # Decimal('1.6')
print(metrics.metric_status("vwap_slippage_per_share"))
```

Fields with no data are reported `NOT_COMPUTABLE` / `PARTIAL_DATA`, never
fabricated.

### 4.7 Dashboard / Prometheus

```python
from axel.dashboard.metrics import collect_metrics, format_prometheus

snapshot = collect_metrics(
    session=db,
    broker=broker,
    kill_switch=kill_switch,
    approval_service=approvals,
    halt=halt,
    incident_store=engine.incidents,
    live_enabled=decision.enabled,
    unreconciled_orders=len(service.pending_recovery()),
    reconciliation_mismatches=len(result.mismatches),
)
print(format_prometheus(snapshot))
```

---

## 5. Database schema

Phase 6 tables are created with `Base.metadata.create_all` via `init_db()`:

```python
from axel.db.session import init_db
init_db()
```

New tables: `order_events`, `approvals`, `incidents`, `reconciliation_runs`,
`execution_metrics`, `live_configs`. Monetary/quantity columns store exact
decimal strings to avoid binary rounding error.

---

## 6. Documentation map

- Specification — [`AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md`](./AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md)
- Gap analysis / change plan — [`phase-6-audit.md`](./phase-6-audit.md)
- Operational runbook — [`phase-6-runbook.md`](./phase-6-runbook.md)
- Go-live plan (blocked) — [`phase-6-rollout.md`](./phase-6-rollout.md)
