# Phase 6 — Staged rollout plan (tiny-live Stocks)

> **STATUS: BLOCKED.** Phase 6 implementation is complete and tested, but go-live
> is blocked on **Phase 5 promotion evidence**. No promotion evidence is recorded
> in this repository (`evaluate_promotion_gate` exists in
> `axel/operations/paper_soak.py`, but `scripts/run_paper_soak.py` is a heartbeat
> placeholder without attributable trade observations). Do not proceed past
> Stage 0 until the Phase 5 evidence package below exists and is signed off.

This plan never invents numeric limits. Every limit is taken from the approved
risk policy and recorded in the versioned `LiveConfig`.

---

## 0. Preconditions (all required)

| # | Precondition | Evidence artifact | State |
| --- | --- | --- | --- |
| 1 | Phase 5 paper soak completed for the required period | soak report | **MISSING** |
| 2 | Promotion gate passes (`evaluate_promotion_gate`) | gate report | **MISSING** |
| 3 | Calibration within policy (Brier) | gate report | **MISSING** |
| 4 | Slippage delta within policy | gate report | **MISSING** |
| 5 | Recent kill-switch drill | drill record | **MISSING** |
| 6 | Recovery drill passed | drill record | **MISSING** |
| 7 | No unresolved incidents | `unresolved_incidents` = 0 | **MISSING** |
| 8 | Phase 6 test suite green | CI/pytest output | done locally |
| 9 | Risk policy and live limits approved | change record | **MISSING** |
| 10 | Named operator authority + recovery token custody | ops record | **MISSING** |

`phase5_gate_passed` must be `True` in the `LiveConfig` and must reference a real
evidence artifact. Absent evidence fails closed.

---

## 1. Stage 0 — Paper, live disabled (default)

- `environment=paper`, `live_trading_confirmed=false`, no active `LiveConfig`.
- Run the full Phase 6 test suite and the boundary lint.
- Exercise the failure drills (timeout, lost ack, duplicate fill, cancel race,
  crash/restart, DB unavailable) against the mock adapter.
- Exercise halt + recovery drills.
- **Exit:** all drills pass; runbook rehearsed.

## 2. Stage 1 — Live armed, no orders (dry run)

- Load a validated `LiveConfig` but keep the pre-trade gate blocking
  (e.g. `live_trading_confirmed=false`, or a manual hold).
- Confirm `axel_live_enabled 0`, account identity matches, reconciliation healthy.
- Open and then cancel a single minimal order through the full path to verify
  the cancel/reconcile loop, then confirm books are flat.
- **Exit:** identity, reconciliation and cancel/reconcile verified; flat books.

## 3. Stage 2 — Smallest live order

- One symbol, one deterministic order, the smallest size permitted by the
  approved policy (not chosen here).
- Full path: live gate → pre-trade → approval → submit → ack → fill → reconcile.
- Verify: no duplicate submit, correct client order id, fill dedup, metrics
  recorded with benchmark source/window, reconciliation healthy.
- **Exit:** order reconciled cleanly; metrics captured; no incidents.

## 4. Stage 3 — Bounded daily operation

- Operate within the approved limits for a bounded period.
- Daily preflight (runbook §2); continuous monitoring (runbook §4).
- Any `CRITICAL` incident, `GLOBAL` halt, or failed preflight → rollback
  (runbook §8) and review before continuing.
- **Exit:** period completes with all acceptance criteria below met.

## 5. Stage 4 — Steady state (still bounded)

- Continue within approved limits. Capital changes are a separate, approved
  change — never automatic, never invented.

---

## 6. Acceptance test (PRD, unchanged)

1. Clean deployment with live disabled → an operator cannot submit an order.
2. After an approved, authenticated configuration, the smallest Stocks order
   passes deterministic validation, reaches the broker, fills and reconciles.
3. A simulated mismatch creates an incident.
4. A kill-switch event stops new risk-increasing orders.
5. Recovery returns to disabled/paper until explicitly reauthorized.

---

## 7. Evidence package (assemble before Stage 1)

- Phase 5 soak report + promotion gate report (criteria, values, pass/fail).
- Kill-switch drill record and recovery drill record.
- The exact `LiveConfig` (version, limits, symbols, approval metadata).
- Reconciliation run records for the dry run and first live order.
- Incident log (empty or fully resolved) for the dry run.
- Rollback evidence: keys rotated, config deactivated, `axel_live_enabled 0`.
- Named operator authority and token custody confirmation.

---

## 8. Rollback criteria (any one triggers it)

- Any `CRITICAL` incident or `GLOBAL` halt.
- Reconciliation not healthy, or reconciliation older than the agreed bound.
- Account identity mismatch or broker health failure.
- Unexpected `axel_live_enabled 1` outside an approved window.
- Any suspected duplicate submission or unknown broker order.

Rollback procedure: [`phase-6-runbook.md`](./phase-6-runbook.md) §8.

---

## 9. Open questions to close before Stage 1

1. Alpaca real-mode lookup-by-client-id / idempotent-retry behaviour — validate
   against the actual paper/live endpoints (implemented but not proven live).
2. Approval operator authentication source (which identity system issues the
   operator principal / recovery token).
3. Execution-metric benchmark source availability (market VWAP) and coverage.
4. Where and how Phase 5 evidence is produced and recorded.

---

## 10. Sign-off

| Role | Name | Date | Reference |
| --- | --- | --- | --- |
| Risk owner | _pending_ | | |
| Operations lead | _pending_ | | |
| Engineering lead | _pending_ | | |

No stage may begin without the responsible sign-off and the matching evidence.
