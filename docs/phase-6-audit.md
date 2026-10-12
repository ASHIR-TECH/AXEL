# Phase 6 Audit — Tiny Live Stocks (P6-AUDIT)

Status: **audit complete — no production behavior changes made.**
Inputs: `docs/AXEL_Phase_6_PRD.pdf` (authoritative scope) and
`docs/AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md` (sections 4–19).

Method: every row below was verified by reading the source file, not by
filename. Where a requirement is met partially, the evidence cites the exact
functions and fields that exist. `[OPEN QUESTION]` marks a choice the
specification says must be resolved against the repository or a selected
broker API.

Executive summary: the repository already contains a deterministic risk
engine, an Alpaca adapter, an approval service, a kill switch, a watchdog and
a reconciliation engine — but they are **Phase 1 paper-era building blocks**,
none of which implements the Phase 6 live boundary: versioned fail-closed
live configuration, order-hash-bound approval, a durable order lifecycle with
explicit `UNKNOWN`/cancel-race handling, broker-side reconciliation of fills
and cash, incident records, halt/recovery authority, and per-fill cost
capture. The audit table below maps every Phase 6 requirement to the
smallest change needed.

---

## 1. Requirement → file → status

### 1.1 Live configuration gate (PRD §“Live Configuration Gate”, spec §4.4, §8.1)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Live mode requires explicit versioned config | `axel/core/config.py` | **PARTIAL** | `environment` (`paper`/`live`), `live_trading_confirmed`, `alpaca_paper`, `is_live_permitted()` exist as a two-flag double lock; **no versioned live-config record** with caps, universe, approval metadata, kill-switch state | New `LiveConfig` schema + version field; validation `fail closed` on missing/stale/conflicting/invalid values; live gating reads the approved record, not bare env flags |
| All PRD §4.4 fields enforced (capital / position / order / daily-loss / concurrency / frequency / universe / approval / kill-switch) | — | **MISSING** | No such record exists anywhere | Add fields to `LiveConfig` as `Decimal`s; every value validated before activation |
| Missing/stale/conflicting/invalid → fail closed | — | **MISSING** | `AxelSettings` validates two booleans only | Config validator returns typed failures; unacceptable values keep live disabled |
| Live activation is an explicit auditable operator action | — | **MISSING** | No activation event, no approval metadata | Gate requires signed activation record + config-approval metadata; never triggered by env default |

### 1.2 Manual approval (PRD §“Manual Approval”, spec §4.5, §6.4, §8.2)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Authenticated explicit approval | `axel/execution/approval.py` | **PARTIAL** | `ApprovalService.approve(proposal_id, operator_id)`; `operator_id` is an unauthenticated free string; no auth reference | Bind approval to an authenticated approver principal + audit reference |
| Approval bound to exact order hash | `axel/execution/approval.py` | **PARTIAL** | `ApprovalTicket` stores `proposal`, **not** an order hash; keyed by `proposal.id` | Store `order_hash` field; validation rejects mismatched hash at submission |
| Expiration window | `axel/execution/approval.py` | **DONE** | `expires_at`, `sweep_expired()`, `DEFAULT_TTLS` | — |
| Single-use, non-reusable | `axel/execution/approval.py` | **MISSING** | No consumed timestamp; no invalidation on reuse | `consumed_at`; reject non-`PENDING` at submission |
| Invalidation on material order change | — | **MISSING** | Nothing compares new order to the approved one | Hash covers symbol/side/qty/type/price; recompute + compare |
| No agent self-approval | — | **MISSING** | `ApprovalService` is in `axel/execution/` (LLM-free boundary holds) but nothing links approver principal vs. proposal author | Principal check at approve time |
| Approval durable + audit recorded | — | **MISSING** | Tickets live in an in-memory `dict` | Persist to `approval` table; append-only events |

### 1.3 Broker adapter (PRD §“Broker Adapter”, spec §5, §9)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Deterministic adapter interface | `axel/execution/broker_base.py` | **PARTIAL** | `BrokerAdapter(ABC)` has `get_account_summary`, `get_positions`, guarded `submit_order`, `cancel_order`, `cancel_all_orders`; **no** `get_order`, `find_order_by_client_id`, `get_open_orders`, `list_fills`, `health_check` | Extend the ABC to spec §9 methods; audit `[OPEN QUESTION]` per Alpaca API support |
| Alpaca implementation | `axel/execution/alpaca.py` | **PARTIAL** | `AlpacaAdapter` implements the above 5; mock mode for drills; `cancel_order` looks up by `client_order_id`; **no** read-only account check endpoint distinction, no open-order list, no fills list, no health | Implement read-only startup validation path (account/positions/open orders/clock) before any live order permission is granted |
| Provider-specific responses stay in adapter | `axel/execution/alpaca.py` | **DONE** | `_execute_order` maps Alpaca status → `OrderState`; `OrderResult` is canonical; no vendor types leak to risk | — |
| Credentials only in execution process | `axel/core/config.py` | **DONE** | `SecretStr` fields (`alpaca_api_key`/`secret_key`); adapter reads them; dashboard/agents don’t receive them | — |
| Timeouts and bounded retries; distinguish retryable vs ambiguous | `axel/execution/alpaca.py` | **MISSING** | `httpx.Client(timeout=10.0)` with no retry classification; a timeout surfaces as an exception with no `UNKNOWN` semantics | Error taxonomy: `retryable` / `non-retryable` / `ambiguous`; only safe read-only calls retry |
| Sanitized logs/responses | `axel/execution/alpaca.py` | **PARTIAL** | `raw_response` stored on `OrderResult` unredacted (`https://…` URLs carry no key, but payloads are not scrubbed); no logging of requests | Redaction helper for responses before logging/persisting |
| Explicit decimal parsing | `axel/execution/alpaca.py` | **MISSING** | `float(...)` everywhere | Parse to `Decimal` per spec §6.1 |
| DO NOT bypass Trading Core approval | `axel/execution/broker_base.py` | **DONE** | `submit_order` raises `PermissionError` unless `risk_decision.approved` and verdict `APPROVED` | — |

### 1.4 Order state machine (PRD §“Order State Machine”, spec §7)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Explicit canonical states incl. ambiguous outcome | `axel/core/types.py`, `axel/execution/order_fsm.py` | **PARTIAL** | States: `PENDING_SUBMIT SUBMITTED ACCEPTED PARTIALLY_FILLED FILLED CANCELED EXPIRED REJECTED`. **Missing** `CREATED`, `APPROVED`, `ACKNOWLEDGED`, `UNKNOWN`, `CANCEL_PENDING`, `ERROR` | Extend `OrderState` + transition table to spec §7.2 |
| Validated transitions | `axel/execution/order_fsm.py` | **DONE** | `VALID_TRANSITIONS` map, `can_transition`, `transition` raises `InvalidOrderTransitionError` | Extend for new states |
| Duplicate-submission prevention | `axel/core/ids.py`, `axel/execution/alpaca.py` | **PARTIAL** | `generate_client_order_id(proposal_id, attempt)` is deterministic; adapter mock dedups; live path relies entirely on Alpaca client_order_id rejection; **no** local `SUBMITTED` outbox guard | Outbox: persist `SUBMITTED` before network send (spec §7.3); reject duplicates by client id locally |
| Unknown outcome handled by query+reconcile, no blind retry | — | **MISSING** | `UNKNOWN` state does not exist; a timeout propagates an exception | Add `UNKNOWN`; on ambiguity query broker by client id and reconcile before retry decision |
| Cancel requested ≠ cancelled | — | **MISSING** | Only terminal `CANCELED`; no `CANCEL_PENDING` | Add `CANCEL_PENDING`; keep ingesting fills until confirmed |
| Restart reconstructs from durable records | — | **MISSING** | `OrderModel` is a current-state row; no append-only order-event history | Append-only order events; projection rebuilt from events + reconciliation |
| Timeout ≠ proof of failure | — | **MISSING** | No timeout modeling | `UNKNOWN` path per §7.1 |

### 1.5 Pre-trade validation (PRD §“Pre-Trade Validation”, spec §4.5, §8.3)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Deterministic risk limits | `axel/risk/engine.py`, `axel/risk/tail.py`, `axel/risk/limits.py` | **DONE** (paper scope) | `SectionRiskAgent.evaluate` (kill switch, RR, confidence, sizing, positions-per-section, correlation), `TailRiskEngine` (VaR/ES/stress/marginal), immutable `RiskLimits` | Reuse unchanged; **do not invent numeric limits** (spec §19) |
| Daily loss / drawdown | `axel/risk/loss_manager.py` | **DONE** | `LossManager.evaluate_daily_stop`, section daily stop | Wire into live pre-trade gate |
| Symbol/session eligibility, data freshness, order frequency, duplicate/stale order, Watchdog health, approval validity, buying power | — | **MISSING** | None of the §8.3 checks exist outside the risk engine | New `PreTradeValidator` orchestrating §4.5 sequence in order, each check with stable reason code; reject on first failure; risk must not depend on LLM |
| Immutable audit record before submit | `axel/db/models.py` (`AuditLogModel`) | **PARTIAL** | Append-only audit table exists; pre-trade does not write per-order records | Write pre-trade decision record + correlation ID before submission |
| Submit only after all checks pass | `axel/execution/broker_base.py` | **PARTIAL** | Guarded by `RiskDecision`, not by a full gate checklist | Gate returns a single `GO / NO-GO` decision feeding `submit_order` |

### 1.6 Post-trade reconciliation (PRD §, spec §10)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Compare orders, fills, positions, cash/P&L, cancellations, rejections | `axel/execution/reconcile.py` | **PARTIAL** | `ReconciliationEngine.reconcile_positions` (qty drift) and `reconcile_orders` (ghost/unmatched by client id, healthy flag); **no** fills, cash/buying power, P&L, cancellation/rejection comparison | Extend engine per spec §10.2: snapshot time, fills, positions, cash, P&L, cancel state |
| Broker authoritative for its side; AXEL for proposals/approvals/audit | — | **PARTIAL** | Report flags drift; nothing overwrites | Keep; add explicit “no silent overwrite” policy doc + tests |
| Any unexplained mismatch → incident | — | **MISSING** | Alerts via `reconciliation_alert` callback; **no incident record** | Create incident entity (spec §6.6, §13.1) |
| Discrepancies never silently overwritten | — | **DONE** | Engine only reports | — |
| §10.3 mismatch cases covered | — | **PARTIAL** | Position drift + ghost/unmatched orders tested; other 7 cases absent | Add per-case tests |
| Block new risk-increasing orders on unexplained mismatch | — | **MISSING** | Engine reports only; no halt linkage | Reconciliation → halt authority binding |

### 1.7 Incidents and halt (PRD §“Incident Handling”, “Kill-Switch” , spec §12–13)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Incident record (category, severity, observed/expected, action, halt status, resolution) | `axel/operations/paper_soak.py` (`record_incident`) | **PARTIAL** | Writes to `AuditLogModel` with severity/summary/details; that is paper-soak scoped, no dedicated incident schema, no resolution lifecycle, no halt linkage | New `incidents` table + lifecycle (spec §6.6, §13.1) with stable reason codes |
| Global halt | `axel/risk/killswitch.py`, `axel/watchdog/monitor.py` | **DONE** | `KillSwitch.trip/re_arm`, persisted state file, Watchdog triggers on drawdown | — |
| Section-scoped halt, new-order halt while reconciliation/cancel remains | — | **MISSING** | `KillSwitch.trip(section)` accepts a section arg but state is global-only; no “halt new orders only” mode | Halt authority model: global / section / new-orders-only, reconciliation always permitted |
| Broker-disconnect, risk-limit, stale-data, Watchdog-failure halts | `axel/watchdog/monitor.py` | **PARTIAL** | Watchdog covers stale equity + drawdown only | Add explicit halt triggers per §13.1; Watchdog halt must not depend on LLM (already true) |
| Halt independent of DB | `axel/risk/killswitch.py` | **DONE** | State file on disk, not DB | — |
| Recovery requires explicit authority + evidence; never auto re-enable | `axel/risk/killswitch.py` | **DONE** (kill switch) | `re_arm` requires token + rationale; **no recovery checklist/runbook ordering** | Add recovery checklist (spec §13.3) + runbook |

### 1.8 Execution analytics (PRD §“Observability”, spec §11)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Per-fill cost metrics (IS, VWAP slippage, fees, fill ratio, timing) | — | **MISSING** | `FillModel` stores `fee` and `slippage` columns but nothing computes them; no benchmark logic anywhere | New `execution/analytics.py` per spec §11.2; capture decision/arrival reference, bid/ask where available |
| Honest missing data (`COMPUTABLE/PARTIAL_DATA/NOT_COMPUTABLE`) | — | **MISSING** | — | Metric record carries data coverage per §6.7 |
| Metrics reproducible from stored raw inputs | `axel/data/ingest/raw_store.py` (pattern) | **MISSING** for execution | Raw provider payloads are persisted for market data; no execution raw capture | Reuse RawStore pattern for broker events + fills |
| Order lifecycle / connectivity / reconciliation status observable | `axel/dashboard/metrics.py`, `axel/dashboard/query.py` | **PARTIAL** | `MetricsSnapshot` = equity, exposure, kill switch, HITL tickets, proposal counts; `DashboardQuery` has write guard; **no** order lifecycle, broker connectivity, reconciliation status, incident IDs | Extend snapshot + query; all fields from real persisted state |
| Correlation ID on every live action | — | **MISSING** | No correlation-id field anywhere in execution | Add to every order/approval/risk/audit record (spec §12) |

### 1.9 Persistence / audit (PRD §, spec §12)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Durable order intent/events/fills/approval/risk/config/incident | `axel/db/models.py` | **PARTIAL** | `OrderModel`, `FillModel`, `RiskDecisionModel`, `TradeProposalModel`, `AuditLogModel`, `KillSwitchEventModel` exist; **no** live-config, approval, incident, reconciliation-run, metric, or order-event tables | Add tables per §12 “Minimum durable entities” |
| Event replay without duplicate fills | — | **MISSING** | No event log; fills keyed by internal id only | Append-only order events; fill dedup by strongest broker execution id (spec §6.3) |
| Current state reconstructible from events | — | **MISSING** | `OrderModel` single row, no version history | Projection over event stream + reconciliation |
| Decimals for money | — | **MISSING** | `Float` everywhere in contracts and models | Use `Decimal` for qty/price/fee/money in new records |
| UTC + event vs receive time | `axel/db/models.py` | **PARTIAL** | `DateTime(timezone=True)`; order/fill have receive timestamps but **no broker event time field** | Add broker-supplied timestamps separately |

### 1.10 Security / trust boundary (PRD §“Security”, spec §14)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| No LLM imports in risk/execution | `scripts/check_no_llm_imports.py`, CI `verify.yml` | **DONE** | AST lint enforces zero LLM imports in `axel/risk` + `axel/execution`; runs in CI | Keep; extend to any new dirs |
| Agents cannot submit orders or see credentials | `axel/agents/` | **DONE** | No execution import in agents (verified by grep); boundary lint | — |
| Approval identity authenticated | — | **MISSING** | `operator_id` is a string | Auth principal requirement |
| Secrets never in logs/prompts | — | **PARTIAL** | `SecretStr` used; no logging of credentials; `raw_response` unredacted | Redaction test (spec §15.4) |
| CI never touches a live account | `verify.yml` | **DONE** | CI runs ruff/boundary/pytest only; no live keys | — |

### 1.11 Tests (spec §15)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Unit: FSM, approval expiry, risk limits, reconciliation drift | `tests/unit/test_order_fsm.py`, `test_approval.py`, `test_risk_limits.py`, `test_reconcile.py`, `test_killswitch.py` | **DONE** (18 tests across these) | — | Extend for new states/fields |
| Broker contract tests | — | **MISSING** | `tests/unit/test_provider_contracts.py` covers **market-data** providers only | Sandbox contract suite per §15.2 with recorded Alpaca payloads |
| Failure-injection tests (§15.3, 15 cases) | — | **MISSING** | No timeout/duplicate ack/partial-fill-race/restart/DB-outage/Watchdog-failure tests | Full failure-injection suite |
| Permission tests (§15.4) | `tests/unit/test_agent_boundary.py` | **PARTIAL** | Covers agent trust boundary partially; **no** live-gate fail-closed / approval forgery tests for Phase 6 | Add §15.4 cases |
| Reconciliation mismatch tests (§15.5) | `tests/unit/test_reconcile.py` | **PARTIAL** | 2 mismatch cases | All §10.3 cases |
| Research-metric tests (§15.6) | — | **MISSING** | — | IS sign convention, dedup, fee-once, no benchmark w/o data |

### 1.12 Phase 5 gate (PRD §“Preconditions”, spec §4.3)

| Requirement | Existing file(s) | Status | Evidence | Smallest change |
| --- | --- | --- | --- | --- |
| Phase 5 promotion evidence verified | `axel/operations/paper_soak.py` | **BLOCKED** | Gate logic exists (`evaluate_promotion_gate`, `PromotionGatePolicy`); **no promotion evidence recorded** in repo; `scripts/run_paper_soak.py` is a heartbeat placeholder without trade attribution | Produce + record Phase 5 evidence before `P6-ROLLOUT`; Phase 6 must stay gated until then |

---

## 2. File-level change plan (proposed — not yet executed)

Ordered by work package from spec §16. Each line is a separate reviewable change per `P6-AUDIT` conventions.

| Package | Files (new/changed) | Content |
| --- | --- | --- |
| `P6-GATE` | `axel/execution/live_gate.py` (new), `axel/db/models.py`, tests | Versioned `LiveConfig` record; fail-closed validation; activation authority + metadata; no numeric invention |
| `P6-APPROVAL` | `axel/execution/approval.py`, `axel/db/models.py` (+`approval`), tests | Order-hash binding, authenticated principal, `consumed_at`, invalidation on change, persistence |
| `P6-BROKER` | `axel/execution/broker_base.py`, `axel/execution/alpaca.py`, tests | Spec §9 methods, read-only startup checks, error taxonomy, Decimal, redaction, contract tests w/ recorded payloads |
| `P6-ORDER` | `axel/core/types.py`, `axel/core/contracts.py`, `axel/execution/order_fsm.py`, `axel/db/models.py` (+order events), tests | Full spec §7.2 states, outbox + `UNKNOWN`, cancel races, restart reconstruction, idempotency, correlation IDs |
| `P6-RISK` | `axel/execution/pre_trade.py` (new), tests | §4.5 sequence as ordered deterministic checks; reuses `SectionRiskAgent`/`TailRiskEngine`/`LossManager`; stable reason codes; no LLM |
| `P6-RECON` | `axel/execution/reconcile.py`, `axel/db/models.py` (+incident, +reconciliation_run), tests | Fills/cash/P&L/cancel comparison, all §10.3 cases, incident creation, risk-block linkage |
| `P6-HALT` | `axel/risk/killswitch.py`, `axel/watchdog/monitor.py`, tests | Section / new-orders-only halt modes; §13.1 triggers; recovery checklist |
| `P6-OBS` | `axel/execution/analytics.py` (new), `axel/dashboard/metrics.py`, `axel/dashboard/query.py`, tests | Spec §11 metrics, metric record w/ coverage flags, reproducible from raw; dashboard fields |
| `P6-QA` | `tests/` (new files) | §15.2–15.6 suites: broker contract, failure-injection, permission, reconciliation, research-metric |
| `P6-RUNBOOK` | `docs/phase-6-runbook.md` (new) | Deploy, halt, reconcile, recover, rollback; recovery never auto-enables live |
| `P6-ROLLOUT` | `docs/phase-6-rollout.md` (new) | Checklist + evidence package; **BLOCKED on Phase 5 evidence** |

---

## 3. Things that must not change (spec §19)

- No new Stocks alpha strategies, no LLM-order authorization, no Phase 5 gate bypass, no invented numeric limits, no auto capital scaling, no multi-broker routing, no blind duplicate submit, no automatic limit→market conversion, no cancel-request=confirmed-cancellation assumption, no overwriting reconciliation mismatches, no counting unknown costs as zero, no secrets in logs/Git/prompts, no live enablement in CI/development/default deployment, no retrospective metrics in the live authorization path.

## 4. `[OPEN QUESTION]`s to resolve before `P6-BROKER`/`P6-ORDER`

1. Alpaca API support for lookup-by-client-id / idempotent retry: confirm against the actual paper/live endpoints (spec §7.3, §9 fallback rules).
2. Approval operator authentication source (existing auth? Telegram operator identity?) — governs `P6-APPROVAL`.
3. Execution metrics benchmark source (market VWAP availability) — governs `P6-OBS`.
4. Phase 5 evidence: where/how is `run_paper_soak.py` producing attributable trade observations today?

---

## 5. Acceptance-test preview (PRD, unchanged)

Clean deployment, live disabled → operator cannot submit. After approved config + authenticated manual approval, the smallest Stocks order passes deterministic validation, reaches the broker, fills, reconciles. A simulated mismatch creates an incident. A kill-switch event stops new risk-increasing orders. Recovery returns to disabled/paper until explicitly reauthorized.