# Phase 6 — Operational runbook (tiny-live Stocks)

Scope: operating the Phase 6 tiny-live Stocks path once it is authorized. This
runbook covers daily operation, halting, recovery and rollback. It does **not**
authorize go-live; that requires the staged plan in
[`phase-6-rollout.md`](./phase-6-rollout.md) and recorded Phase 5 evidence.

Guiding rule (spec §13.3): **recovery never auto-enables live.** Re-enabling live
always requires a validated `LiveConfig`, a fresh human approval and a passing
pre-trade gate.

---

## 1. Safety invariants (never violate)

1. Live is off unless a validated, versioned `LiveConfig` says otherwise.
2. Agents may propose; only a human principal may approve. Agents can never
   submit or cancel a broker order.
3. No blind duplicate submission. An ambiguous submit is resolved by lookup.
4. A cancel request is **not** a confirmed cancellation until the broker confirms
   or reconciliation proves the terminal state.
5. A halt never removes read-only reconciliation or the ability to reduce risk.
6. Mismatches are never overwritten; they become incidents.
7. Unknown costs are never counted as zero; missing data is labelled missing.

---

## 2. Start-of-day preflight

Run in order; stop and halt if any step fails.

1. **Broker connectivity** — `broker.health_check()` must be healthy, or the halt
   controller trips `BROKER_OUTAGE`.
2. **Account identity** — `broker.get_account().account_id` must equal the
   `account_id` in the active `LiveConfig`.
3. **Reconciliation** — run the full loop; require `result.is_healthy`. If it
   blocks new risk, do not trade until resolved.
4. **Market data freshness** — quotes within the configured freshness bound.
5. **Watchdog** — heartbeat present; otherwise `bad` new risk is blocked.
6. **Halt state** — confirm the halt controller is clear.
7. **Incidents** — no open `CRITICAL` incidents.
8. **Live gate** — `gate.evaluate(...)` must return `enabled=True`.

```bash
.venv/bin/python -m pytest -q tests/unit/test_reconcile_phase6.py tests/unit/test_halt.py
.venv/bin/python scripts/check_no_llm_imports.py
```

---

## 3. Activating live (per session/day)

Activation is a deliberate, recorded human action.

1. Build the `LiveConfig` for the session (limits from the approved risk policy —
   never invented here).
2. Attach `ApprovalMetadata` (approver identity, UTC time, change reference).
3. Confirm `phase5_gate_passed=True` backed by recorded Phase 5 evidence.
4. Evaluate the gate with live operational facts; require `enabled=True`.
5. Persist the config to `live_configs` (record `active=True`, `activated_at`).
6. Publish the dashboard snapshot; confirm `axel_live_enabled 1`.

---

## 4. Monitoring (per session)

Watch these Prometheus gauges continuously:

| Metric | Meaning | Action |
| --- | --- | --- |
| `axel_live_enabled` | Live activation state | Unexpected `1` outside a window → halt |
| `axel_halt_active`, `axel_halt_scope` | Active halt + scope | Investigate source trigger |
| `axel_critical_incidents_total` | Risk-blocking incidents | Resolve before new risk |
| `axel_open_incidents_total` | All open incidents | Triage |
| `axel_unreconciled_orders_total` | Orders awaiting reconciliation | Investigate growth |
| `axel_last_reconciliation_age_seconds` | Reconciliation freshness | Stale → halt new risk |
| `axel_reconciliation_mismatches_total` | Last-run drift | Any critical → investigate |

Also review the versioned execution metrics (`axel/execution/analytics.py`):
fill ratio, time-to-ack, time-to-fill, implementation shortfall (signed) and VWAP
slippage where a benchmark exists. Treat any single statistic as diagnostic only;
do not rank execution quality from an insufficient sample.

---

## 5. Halting

### 5.1 Automatic triggers (spec §13.1/§13.2)

The halt controller trips on: repeated rejections, excessive loss, stale market
data, broker outage, watchdog failure, database failure during order handling,
reconciliation drift, and manual operator halt. Every trip records a stable
`HaltReason` and a scope.

### 5.2 Scopes

| Scope | Effect | Read-only + risk reduction |
| --- | --- | --- |
| `NEW_RISK_ONLY` | Blocks new risk-increasing orders | Always permitted |
| `SECTION` | Blocks new risk in one section | Always permitted |
| `GLOBAL` | Blocks all new risk | Always permitted |

### 5.3 On a halt

1. Halt request is persisted to `.axel_halt.json` (independent of the database).
2. New risk-increasing orders are blocked immediately.
3. Watchdog and execution workers are notified.
4. Cancellations follow the **reviewed halt policy** only — the conservative
   default cancels nothing automatically; a `GLOBAL` policy may cancel all
   working orders.
5. Continue account/order/fill queries and reconciliation.
6. Record every cancellation outcome as `succeeded`, `failed` or `unknown`.
7. Alert the operator.
8. Recovery requires explicit authorization (below).

```python
from axel.execution.reason_codes import HaltReason
from axel.risk.halt import CancelOutcome

halt.trip(HaltReason.MANUAL_OPERATOR, detail="drill / incident", source="operator")
for client_order_id in halt.plan_cancellations(open_client_order_ids):
    ok = broker.cancel_order(client_order_id)
    halt.record_cancel_outcome(
        client_order_id, CancelOutcome.SUCCEEDED if ok else CancelOutcome.UNKNOWN
    )
```

Never assume a cancel that was not confirmed succeeded.

---

## 6. Recovery checklist (spec §13.3)

Perform every step; do not skip.

- [ ] Root cause identified and written down.
- [ ] Broker connectivity verified (`health_check` healthy).
- [ ] Full reconciliation run and `result.is_healthy` is true.
- [ ] All blocking (`CRITICAL`) incidents resolved with a named authority.
- [ ] Market data fresh; watchdog healthy.
- [ ] Broker working orders understood (none unknown).
- [ ] Operator token available; written rationale prepared.
- [ ] Call `halt.recover(...)` with `reconciliation_verified=True` and
      `blocking_incident_count=0`.
- [ ] Re-activate live through the gate (config + approval + pre-trade). Recovery
      alone leaves live disabled.

```python
halt.recover(
    operator_token=settings.operator_rearm_secret.get_secret_value(),
    written_rationale="broker connectivity verified and books reconciled",
    reconciliation_verified=True,
    blocking_incident_count=0,
)
```

---

## 7. Incident response

Each mismatch/incident carries a stable reason code and a severity. Open
`CRITICAL` incidents block new risk until resolved by a named authority with a
note and evidence.

| Incident | Typical cause | Immediate action |
| --- | --- | --- |
| Unknown broker order | Ghost/duplicate submission | Halt new risk; identify via client order id; reconcile |
| Position mismatch | Fill not ingested / external trade | Reconcile fills; do not overwrite |
| Unrecorded / duplicate fill | Delivery at-least-once | De-dup by broker fill id |
| Stale market data | Feed gap | Halt new risk until fresh |
| Broker outage | Connectivity | Halt new risk; keep queries |
| Repeated rejections | Bad order params / limits | Halt new risk; review rejects |
| Excessive loss | Adverse P&L | Global halt; review |
| Watchdog failure | Missed heartbeat | Halt new risk; restart watchdog |
| Database failure | Storage down | Halt new risk; do not submit blind |
| Auth/permission failure | Bad/expired credentials | Halt; rotate credentials |

Resolve incidents explicitly:

```python
engine.incidents.resolve(
    incident_id,
    resolved_by="ops-lead",
    note="broker confirmed terminal fill; books reconciled",
    evidence={"recon_run": run_id},
)
```

---

## 8. Rollback

To stop trading and revert to paper:

1. Trip a `GLOBAL` halt (manual operator reason).
2. Apply the reviewed cancellation policy; record outcomes.
3. Flush/collect final reconciliation and archive incidents + reconciliation runs.
4. Deactivate the `LiveConfig` (`active=False`) and revoke/rotate broker keys.
5. Set `environment=paper`, `live_trading_confirmed=false`.
6. Confirm `axel_live_enabled 0`.

Rollback never deletes audit records.

---

## 9. Escalation

- Any `CRITICAL` incident, active `GLOBAL` halt, or failed preflight → page the
  on-call operator.
- Only a named human authority may clear a halt or re-activate live.
- If the database is unavailable, halting and reconciliation planning still work
  (halt state and the incident/idempotency logic are designed to survive it); do
  not submit new orders.

---

## 10. References

- Spec §13 (incidents/halt), §10 (reconciliation), §11 (metrics).
- [`phase-6-how-to-run.md`](./phase-6-how-to-run.md)
- [`phase-6-rollout.md`](./phase-6-rollout.md)
