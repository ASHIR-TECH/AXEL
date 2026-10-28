# Phase 11 — Cross-Asset Portfolio Intelligence and Capital Allocation

## 1. Status and scope

Phase 11 adds a deterministic portfolio-intelligence package under
`axel/portfolio/`. It consolidates state, normalizes cross-asset exposure,
estimates risk, proposes risk budgets and allocations, runs stress scenarios,
records evidence, and produces paper rebalance simulations.

It does **not** submit orders, replace the existing risk/execution core, or
replace the independent Watchdog. A successful Phase 11 validation means a
proposal may be handed to AXEL's existing deterministic pathway; it is not an
execution instruction.

The package is pure Python/stdlib (no NumPy). It is imported by the existing
application only when the Phase 11 workflow is used.

## 2. Module map

| Module | Responsibility |
|---|---|
| `axel/portfolio/types.py` | Frozen domain types, invariants, units, policy contracts |
| `axel/portfolio/state.py` | Versioned snapshots, capital aggregation, FX conversion |
| `axel/portfolio/recon.py` | Position/order/commitment/account reconciliation |
| `axel/portfolio/registry.py` | Instrument and factor registry |
| `axel/portfolio/normalize.py` | Stocks, options, crypto, FX, prediction exposure |
| `axel/portfolio/returns.py` | Return panels, covariance, shrinkage, diagnostics |
| `axel/portfolio/risk.py` | Volatility, contributions, VaR/ES, concentration |
| `axel/portfolio/budget.py` | Global and section risk budgets |
| `axel/portfolio/cost.py` | Planning cost, funding, borrow, liquidity assumptions |
| `axel/portfolio/constraints.py` | Independent hard-constraint checks |
| `axel/portfolio/optimizer.py` | Baselines and constrained candidate allocation |
| `axel/portfolio/stress.py` | Versioned scenario suite and revaluation |
| `axel/portfolio/proposal.py` | Allocation/rebalance proposals and explanations |
| `axel/portfolio/validator.py` | Independent deterministic authorization |
| `axel/portfolio/db_models.py` | Append-only snapshot/proposal/audit ORM rows |
| `axel/portfolio/persistence.py` | Domain-to-row mapping and audit evidence |
| `axel/portfolio/observability.py` | Metrics, events, alerts, Prometheus text |
| `axel/portfolio/research.py` | Point-in-time checks, walk-forward, PSR/DSR |
| `axel/portfolio/simulation.py` | Paper rebalance simulation with partial fills |
| `axel/portfolio/release.py` | Shadow evidence and release gate |

## 3. Trust boundary

- LLM output may create a proposal candidate, but no LLM import is allowed in
  `axel/risk/`, `axel/execution/`, or `axel/portfolio/`.
  `scripts/check_no_llm_imports.py` enforces this.
- `axel.portfolio` does not import `axel.execution`; a boundary test enforces it.
- The validator is independent of the optimizer and re-checks freshness,
  provenance, reconciliation, data quality, section readiness, solver status,
  budget reconciliation, stress, constraints, idempotency, and capital.
- Risk-increasing proposals fail closed. Risk-reducing proposals may proceed
  during degraded state only when validation policy explicitly allows it.
- The existing Watchdog and kill switch remain authoritative. Phase 11 only
  consumes their state.

## 4. Accounting and unit conventions

- Base-currency values are used in normalized exposures and risk budgets.
- `NormalizedExposure.delta` is base-currency delta; `delta_equivalent` is
  `delta * underlying_price`.
- Option gamma is base currency per underlying-unit squared; vega and theta are
  base-currency values. Option positions require contract-level inputs.
- Prediction `max_loss` comes from the explicit payoff schedule. Missing
  probabilities do not produce invented expected values.
- FX exposures keep both currency legs and flag undefined max loss/margin.
- `CapitalSummary` totals are `None` when conversion is incomplete. They are
  never coerced to zero.
- Stress scenarios are hypothetical unless an explicit evidenced probability is
  supplied. Large shocks and option P&L are approximate and require
  venue-specific repricing before reliance.

## 5. Persistence and deployment

`init_db()` imports `axel.portfolio.db_models` before `Base.metadata.create_all`,
so Phase 11 tables are created in development/test environments. Production
deployment still needs a reviewed Alembic baseline/migration because
`migrations/versions/` currently has no baseline revision. Until that exists:

1. Apply the ORM schema through the approved deployment mechanism.
2. Verify all `portfolio_*` tables and indexes exist.
3. Run a restart/rebuild drill using persisted snapshots and proposals.
4. Do not treat `create_all` as a production migration substitute.

The append-only rows cover snapshots, sources, positions, open orders,
commitments, exposures, budgets, proposals, targets, constraint results, stress
runs, rebalances, reconciliation issues, decision audits, model versions, and
alerts. `audit_questions()` returns evidence for what AXEL knew, what was
missing, constraints, scenarios, the optimizer proposal, the deterministic
decision, execution lineage, and realized-vs-estimated outcomes.

## 6. Quantitative validation

`axel/portfolio/research.py` provides:

- point-in-time availability checks;
- walk-forward windows with an explicit embargo;
- net performance metrics (Sharpe, Sortino, Calmar, drawdown, tracking error,
  turnover, costs) with conventions documented in warnings;
- deterministic circular block-bootstrap confidence intervals;
- probabilistic and deflated Sharpe diagnostics;
- an experiment registry that records every candidate and refuses to select a
  candidate without a baseline or with a used holdout.

The final holdout must remain untouched. A complex method is not promoted just
because it wins an in-sample search; multiple-testing warnings and the full
candidate set are retained.

## 7. Shadow mode and release gate

`axel/portfolio/release.py` aggregates shadow observations and evaluates
pre-registered criteria. It can only return
`approved_for_human_review` or `blocked`; it cannot enable live execution.
Criteria must be approved before the shadow period starts, and missing realized
costs or stress evidence block approval.

**Shadow mode has not yet been run for this implementation.** The release gate
is code and test coverage, not evidence that the required shadow period passed.
A production release still requires:

- passing CI and security checks;
- reviewed migration(s);
- the pre-registered shadow period and evidence;
- human approval;
- documented alert ownership and rollback drill.

## 8. Known limitations

- No production Alembic baseline exists yet.
- Cost defaults are planning assumptions, not calibrated venue estimates.
- Option stress revaluation uses Black-Scholes when contract inputs exist;
  otherwise it falls back to local Greeks with explicit limitations.
- Stress scenarios do not enumerate all future states and are not probabilities.
- No broker adapter, order IDs, or live execution path exists in Phase 11.
- Prediction-market settlement, fees, and platform rules remain
  platform-specific.
- The package is not wired into a scheduled production job or API in this
  change; deployment integration is a follow-up.

## 9. Verification

```bash
pytest -q
ruff check .
python scripts/check_no_llm_imports.py
```

The Phase 11 tests cover invariants, quantitative correctness, failure
injection, paper fills, persistence round trips, observability, research
validation, release gates, and the no-execution boundary.
