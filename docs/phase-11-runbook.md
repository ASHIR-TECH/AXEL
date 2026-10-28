# Phase 11 Operations Runbook

## 1. Scope

This runbook covers the portfolio-intelligence service and its proposal
workflow. It does **not** authorize live orders. Phase 11 produces proposals,
stress reports, and paper simulations; the existing deterministic core,
execution pathway, and independent Watchdog remain authoritative.

## 2. Alert ownership

| Alert / metric | Owner | First action |
|---|---|---|
| `RECONCILIATION_BLOCKING_ISSUE` | Portfolio operations | Compare internal and authoritative positions/orders; do not increase risk until resolved |
| `STRESS_SUITE_FAILED` | Risk | Inspect worst scenario, assumptions, and affected sections; keep risk increase blocked |
| `PROPOSAL_REJECTED` | Portfolio operations | Read deterministic reason codes; fix data/proposal rather than relaxing limits |
| `axel_portfolio_snapshot_age_seconds` | Data operations | Refresh sources or mark snapshot stale; never backdate |
| `axel_portfolio_exposure_failures` | Portfolio operations | Resolve unsupported instruments or missing FX/contract inputs |
| `axel_portfolio_exposure_coverage_ratio` | Data operations | Investigate incomplete coverage before using the snapshot |
| `axel_portfolio_validation_accept` | Portfolio operations | Treat acceptance as permission to hand off, not as an order |
| `axel_portfolio_stress_scenario_loss` | Risk | Review scenario version and model limitations |
| Solver failure status | Quant engineering | Preserve constraints; fix data/model, do not widen limits |

Metrics are emitted by `axel/portfolio/observability.py` and can be rendered
with `render_prometheus()`. Event IDs are deterministic; consumers must be
idempotent.

## 3. Daily checks

1. Confirm the latest snapshot is complete, fresh, and reconciled.
2. Confirm every active section has a known readiness state. Disabled and
   research-only sections must not be treated as live.
3. Confirm FX conversion is complete for all base-currency totals.
4. Review open reconciliation issues and exposure failures.
5. Review solver status and proposal rejection reasons.
6. Review stress results and the worst scenario.
7. Confirm no live orders were submitted by Phase 11. The expected count is
   always zero.
8. Review proposal age; expired proposals must not be consumed.

## 4. Kill switch and Watchdog

- If the global kill switch or Watchdog halt is active, risk-increasing
  proposals must be rejected. Risk-reducing proposals are allowed only when the
  validation policy explicitly permits them.
- Do not disable the Watchdog or edit risk limits to make a proposal pass.
- Do not create a second safety mechanism that competes with the Watchdog.
- Record the operator, timestamp, reason, and authorization for any policy
  change.

## 5. Recovery and restart

1. Stop the Phase 11 job; do not delete audit rows.
2. Restore the database to a known-good backup if corruption is suspected.
3. Rebuild the latest snapshot from authoritative sources.
4. Compare the rebuilt snapshot with persisted positions, orders, commitments,
   and capital; create reconciliation issues for differences.
5. Re-run exposure normalization and stress; verify the fingerprint/version.
6. Re-validate any pending proposal. Expired or provenance-mismatched proposals
   must be regenerated.
7. Confirm the Watchdog is independent and healthy before resuming proposal
   generation.

## 6. Rollback

- Disable the Phase 11 scheduler/worker first. This prevents new proposals while
  leaving existing audit evidence intact.
- Revert the application deployment to the last approved version.
- Keep Phase 11 tables and audit rows; do not drop them as part of an
  application rollback.
- If a schema migration must be rolled back, follow the reviewed database
  migration procedure and preserve an export of audit rows.
- Re-run smoke tests: snapshot build, exposure normalization, validator
  rejection of a stale proposal, and paper simulation with no live orders.

## 7. Shadow-mode release procedure

1. Pre-register `ShadowCriteria` (period, minimum sample counts, failure rates,
   stress pass rate, cost forecast error, completeness/freshness/reconciliation
   thresholds) **before** collecting evidence.
2. Run Phase 11 in shadow mode with live execution disabled.
3. Record proposals, validations, stress reports, paper simulations, predicted
   costs, realized costs, reconciliation state, and any Watchdog halts.
4. Aggregate with `collect_shadow_metrics()`.
5. Evaluate with `evaluate_release_gate()`. A `blocked` result must be resolved
   by fixing the cause, not by editing thresholds after seeing results.
6. An `approved_for_human_review` result is evidence for a human release review.
   It does not enable live execution.
7. Obtain the required human approval and complete the existing AXEL promotion
   process. Keep live execution disabled unless separately authorized.

## 8. Incident checklist

- [ ] Timestamp and operator recorded.
- [ ] Affected snapshot/proposal IDs recorded.
- [ ] Reason codes and stress scenario IDs recorded.
- [ ] Risk increase stopped if data is stale, incomplete, or unreconciled.
- [ ] No manual order was submitted by Phase 11.
- [ ] Watchdog independence confirmed.
- [ ] Audit rows preserved.
- [ ] Follow-up owner and deadline assigned.
