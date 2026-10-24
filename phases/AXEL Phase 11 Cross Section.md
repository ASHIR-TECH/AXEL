# AXEL Phase 11 — Cross-Section Portfolio Intelligence and Capital Allocation
## Product Requirements Document and Engineering Implementation Specification

**Project:** AXEL v2  
**Phase:** 11  
**Component:** Cross-Section Portfolio Intelligence and Capital Allocation  
**Document type:** PRD, technical specification, implementation plan  
**Status:** Proposed specification  
**Primary objective:** Build a portfolio-level decision-support system that consolidates exposure and proposes risk-aware capital allocations across AXEL's enabled trading sections while preserving deterministic authorization and independent safety controls.

---

## 1. Executive Summary

Phase 11 introduces a portfolio intelligence layer above AXEL's individual market sections.

The initial AXEL sections are:

1. Stocks.
2. Options.
3. Crypto.
4. Forex.
5. Bets and Prediction Markets.

These sections have different instruments, market structures, settlement rules, liquidity profiles, trading costs, and risks. Portfolio intelligence must account for those differences instead of treating all positions as interchangeable.

The Phase 11 system should:

- Maintain a consolidated portfolio view.
- Reconcile balances, positions, open orders, and commitments.
- Normalize exposures using documented, domain-appropriate methods.
- Identify concentration and shared economic risk.
- Propose capital and risk budgets.
- Estimate the effect of costs and turnover.
- Evaluate portfolio stress scenarios.
- Generate constrained allocation proposals.
- Recommend rebalancing.
- Track execution outcomes through existing authorized pathways.
- Reconcile the actual portfolio after execution.
- Provide evidence and explanations for every material allocation decision.

**Phase 11 does not authorize trades.** It creates portfolio-level proposals for the existing deterministic core to validate. The independent Watchdog retains the ability to halt new activity.

Phase 11 must also work when some sections are disabled, unavailable, or paper-only.

## 2. Goals and Non-Goals

### 2.1 Goals

- Provide a consistent view of capital across enabled sections.
- Prevent double counting of cash, positions, and pending orders.
- Identify overlapping exposure across instruments and strategies.
- Respect section-level risk constraints.
- Include transaction costs and liquidity in allocation decisions.
- Support transparent, testable allocation methods.
- Compare optimized allocations with simpler baselines.
- Simulate rebalancing before any production use.
- Provide a complete audit trail for proposals and outcomes.
- Fail safely when portfolio state is incomplete or inconsistent.

### 2.2 Non-goals

Phase 11 does not:

- Replace individual section risk managers.
- Replace the deterministic trading core.
- Override broker, exchange, or provider restrictions.
- Disable or configure the Watchdog.
- Assume that estimated correlations are stable.
- Guarantee optimal allocations or positive returns.
- Automatically transfer funds between venues or accounts unless separately authorized through an existing approved capability.
- Treat betting contracts as ordinary financial securities.
- Treat options exposure as equivalent to underlying share exposure.
- Execute orders directly from the optimizer.
- Make an unavailable section eligible for live trading.
- Use a portfolio optimization result as proof that a trade is safe.

## 3. Architectural Position

### 3.1 Section agents

Each market section continues to generate its own signals and proposals under its existing controls.

Section responsibilities include domain-specific:

- Data validation.
- Signal generation.
- Instrument eligibility.
- Risk calculation.
- Execution requirements.
- Settlement or expiry handling.
- Position and order reconciliation.

### 3.2 Portfolio intelligence layer

Phase 11 consumes section-level information and creates:

- `PortfolioSnapshot`
- `ExposureSnapshot`
- `RiskBudgetProposal`
- `AllocationProposal`
- `RebalanceProposal`
- `PortfolioStressReport`

It must not directly write production orders, fills, positions, or risk configuration.

### 3.3 Deterministic core

The core validates proposals against:

- Current portfolio state.
- Current section-level risk state.
- Approved capital limits.
- Instrument eligibility.
- Available liquidity and capital.
- Current orders and commitments.
- Execution constraints.
- Kill-switch state.
- Watchdog state.
- Reconciliation status.

Only the existing authorized execution path can turn an approved request into orders.

### 3.4 Independent Watchdog

The Watchdog must be able to halt new activity independently of the optimizer.

Portfolio intelligence must not:

- Override a halt.
- Change Watchdog thresholds.
- Hide a portfolio limit breach.
- Convert missing data into a zero-risk reading.
- Continue submitting new allocation proposals as if the system were healthy after a critical state-integrity failure.

## 4. Portfolio State Model

The system must distinguish at least four kinds of portfolio information.

### 4.1 Account and capital state

- Total reported equity.
- Available cash.
- Reserved cash.
- Margin used.
- Margin available.
- Pending deposits or withdrawals, if supported.
- Venue and account identifiers.
- Base currency.
- Last reconciliation timestamp.
- Data freshness status.

### 4.2 Positions

- Instrument identifier.
- Section identifier.
- Venue or provider.
- Quantity or contract count.
- Direction.
- Entry price or average cost, where available.
- Current valuation.
- Currency.
- Unrealized P&L.
- Realized P&L reference.
- Valuation timestamp.
- Position source.
- Reconciliation status.

### 4.3 Orders and commitments

- Open orders.
- Partially filled orders.
- Pending cancel requests.
- Pending replace requests.
- Unknown execution states.
- Reserved buying power.
- Margin commitments.
- Unsettled balances.
- Betting stakes committed to unresolved events.
- Option exercise or assignment obligations.
- Other pending contractual obligations.

The system must avoid counting the same capital twice. A cash balance and a reserved amount are not two independent pools of capital.

### 4.4 Portfolio control state

- Section readiness.
- Section-specific limits.
- Portfolio-wide limits.
- Kill-switch state.
- Watchdog state.
- Data quality status.
- Reconciliation health.
- Last successful snapshot.
- Known unresolved incidents.

A portfolio snapshot must be versioned and timestamped. Allocation decisions must record the exact snapshot used.

## 5. Module Breakdown

| Module | Responsibility | Trust level |
|---|---|---|
| `P11-STATE` | Consolidated portfolio snapshots | Data aggregation |
| `P11-RECON` | Position, order, and balance reconciliation | Control-supporting |
| `P11-NORMALIZE` | Domain-aware exposure normalization | Risk analytics |
| `P11-CORRELATION` | Dependence and shared-risk estimation | Analytics |
| `P11-RISK-BUDGET` | Proposed allocation of approved risk budgets | Proposal-only |
| `P11-COST` | Transaction cost, liquidity, and market-impact estimates | Analytics |
| `P11-OPTIMIZER` | Constrained candidate allocation generation | Proposal-only |
| `P11-STRESS` | Portfolio stress and scenario analysis | Risk analytics |
| `P11-REBALANCE` | Proposed portfolio changes | Proposal-only |
| `P11-EXPLAIN` | Allocation rationale and constraint reporting | Reporting |
| `P11-WATCHDOG` | Integration with independent monitoring | Independent control |
| `P11-QA` | Portfolio invariants and failure tests | Verification |

Do not create a second Watchdog if AXEL already has an independent Watchdog. `P11-WATCHDOG` represents integration requirements, not permission for the optimizer to own the safety mechanism.

## 6. Module Specifications

### 6.1 P11-STATE — Consolidated Portfolio Snapshot

**Purpose:** Produce a consistent snapshot of portfolio state across enabled sections.

Inputs may include:

- Broker account balances.
- Exchange account balances.
- Section position records.
- Open orders.
- Pending commitments.
- Valuation feeds.
- Section readiness status.
- Reconciliation records.

Each snapshot should include:

- Snapshot ID.
- Snapshot timestamp.
- Per-source timestamps.
- Account and venue identifiers.
- Base currency.
- Capital summary.
- Position references.
- Open-order references.
- Pending commitment summary.
- Section status.
- Data quality status.
- Reconciliation status.
- Snapshot schema version.
- Source lineage.
- Known limitations.

**Requirements**

1. Do not combine snapshots with incompatible timestamps without explicitly identifying the inconsistency.
2. Do not assume that a missing section has zero exposure.
3. A disabled section must be marked disabled, not represented as a successful zero-position reconciliation.
4. Stale valuations must be identified.
5. Currency conversions must record the exchange rate and timestamp used.
6. Snapshot generation must be idempotent for the same defined input version.
7. Snapshots must be immutable after publication; corrections create a new version.

**Failure behavior**

If a required source is unavailable, produce a degraded or invalid snapshot according to policy. Do not silently claim that the consolidated portfolio is complete.

### 6.2 P11-RECON — Reconciliation

**Purpose:** Compare AXEL's internal view with authoritative account, broker, exchange, or provider state.

Reconcile, where supported:

- Balances.
- Positions.
- Open orders.
- Partial fills.
- Cancelled orders.
- Pending cancellation or replacement.
- Fees.
- Funding payments.
- Dividends.
- Corporate actions.
- Option exercises and assignments.
- Betting settlement outcomes.
- Unsettled balances.
- Venue-specific obligations.

Every discrepancy should record:

- Discrepancy ID.
- Source systems.
- Expected value.
- Observed value.
- Difference.
- Currency or unit.
- Detection timestamp.
- Severity.
- Resolution state.
- Resolution evidence.
- Responsible operator or service.

Unknown order states must remain unknown until resolved. They must not be assumed cancelled or unfilled.

**Safety requirement**

A critical unresolved discrepancy must block new allocation or rebalance actions wherever those actions depend on the affected state.

### 6.3 P11-NORMALIZE — Cross-Section Exposure Model

**Purpose:** Represent exposure in a common risk framework without erasing instrument-specific behavior.

The normalization model should preserve:

1. Raw instrument exposure.
2. Domain-specific risk representation.
3. Common factor representation, where defensible.
4. Currency exposure.
5. Liquidity and liquidation characteristics.
6. Model uncertainty.
7. Data quality and timestamp.

Examples:

#### Stocks

Potential representations:

- Market value.
- Gross and net exposure.
- Sector concentration.
- Factor exposure.
- Currency exposure.
- Liquidity and estimated liquidation cost.

#### Options

Potential representations:

- Contract count and notional.
- Underlying-equivalent exposure.
- Delta exposure.
- Gamma and convexity exposure.
- Vega exposure.
- Theta and time decay.
- Expiry and strike concentration.
- Assignment and exercise obligations.
- Scenario-based loss estimates.

Greeks are model-dependent local sensitivities, not complete measures of risk. Large price moves, volatility changes, jumps, and near-expiry effects can invalidate simple linear approximations.

#### Crypto

Potential representations:

- Asset quantity and market value.
- Venue exposure.
- Custody concentration.
- Funding exposure.
- Derivative and liquidation exposure.
- Liquidity and gap risk.
- Stablecoin and counterparty exposure, where relevant.

#### Forex

Potential representations:

- Currency-pair notional.
- Long and short exposure by currency.
- Currency-factor exposure.
- Leverage and margin.
- Funding and rollover.
- Liquidity under stress.

#### Bets and Prediction Markets

Potential representations:

- Stake committed.
- Maximum possible loss.
- Maximum payout.
- Net payout exposure.
- Event and outcome exposure.
- Settlement timing.
- Counterparty or provider exposure.
- Correlated event exposure.

A betting contract's payout profile must be modeled explicitly. Its quoted probability must not be confused with the certainty of settlement or the amount of capital at risk.

**Acceptance criteria**

- Each representation has a documented formula and unit.
- Conversions preserve the source value.
- Conversion assumptions and model versions are recorded.
- Unsupported conversions are rejected or marked unavailable.
- No section is represented as zero risk solely because its exposure model is missing.

### 6.4 P11-CORRELATION — Dependence and Shared Risk

**Purpose:** Detect positions whose risks may move together, including positions held in different sections.

The system may estimate dependence using:

- Historical return correlations.
- Rolling correlations.
- Factor exposures.
- Conditional or regime-specific dependence.
- Scenario-based co-movement.
- Domain-specific event relationships.
- Tail-dependence estimates where data supports them.

**Requirements**

- Record the data window and estimation method.
- Track the number of observations.
- Detect missing and asynchronous data.
- Monitor estimation instability.
- Use shrinkage or robust estimation where appropriate.
- Avoid treating correlation as causation.
- Avoid assuming historical correlation remains stable during crises.
- Use stress scenarios when historical observations are insufficient.

For positions with nonlinear payoffs or discrete outcomes, ordinary linear correlation may be insufficient. Use a suitable payoff or scenario representation.

**Failure behavior**

If dependence estimates are unreliable, the system must report the limitation and apply conservative configured constraints rather than treating the missing correlation as zero.

### 6.5 P11-RISK-BUDGET — Risk Budget Proposals

**Purpose:** Propose how approved risk capacity should be distributed across sections and strategies.

Inputs:

- Approved total capital and risk limits.
- Current portfolio snapshot.
- Section readiness.
- Exposure and correlation estimates.
- Liquidity and cost estimates.
- Stress-test results.
- Existing commitments.
- User- or operator-approved policy configuration.

Possible allocation policies:

- Fixed section budgets.
- Volatility-based budgets.
- Risk-parity candidates.
- Concentration-limited budgets.
- Cost-aware budgets.
- Robust optimization candidates.

A risk budget is not an order and not permission to spend the full budget.

**Requirements**

- Distinguish budget, exposure, reserved capital, and available cash.
- Respect section-level caps.
- Record all binding constraints.
- Prevent an optimizer from increasing a hard limit.
- Account for existing open orders and commitments.
- Require deterministic validation before implementation.

### 6.6 P11-COST — Transaction Cost and Liquidity Model

**Purpose:** Estimate the cost of changing the portfolio.

Where supported, include:

- Bid-ask spread.
- Broker or exchange fees.
- Market impact.
- Slippage.
- Funding and rollover.
- Borrowing costs.
- Options-specific spread and hedging costs.
- Betting-market spread or commission.
- Venue-specific fees.
- Currency conversion costs.
- Settlement or transfer costs, if applicable.

Cost estimates should include:

- Cost-model version.
- Input timestamp.
- Instrument and venue.
- Expected trade size.
- Liquidity assumptions.
- Estimated cost.
- Uncertainty or stress range.
- Model limitations.

**Requirements**

- Compare allocations on a net-of-cost basis where feasible.
- Penalize unnecessary turnover.
- Model partial fills when relevant.
- Include a conservative stress-cost case.
- Avoid assuming infinite liquidity.
- Flag when proposed order size exceeds validated capacity.

If the cost model is unavailable for a material position, the proposal must be marked incomplete or use an explicitly approved conservative fallback.

### 6.7 P11-OPTIMIZER — Constrained Allocation Candidates

**Purpose:** Generate candidate allocations under hard constraints.

Start with interpretable methods:

- Fixed risk budgets.
- Volatility-based allocation.
- Risk-parity candidates.
- Constrained mean-variance optimization.
- Robust optimization.
- Correlation-aware concentration controls.
- Cost-aware rebalancing.
- Scenario-based allocation limits.

The optimizer must not assume that expected returns or correlations are known precisely.

**Inputs**

- Versioned portfolio snapshot.
- Eligible sections and instruments.
- Expected-return assumptions, if used.
- Risk and dependence estimates.
- Cost model.
- Approved hard constraints.
- Turnover limits.
- Liquidity and capacity limits.
- Stress scenarios.
- Optimization objective and version.

**Outputs**

- Candidate allocation ID.
- Input snapshot ID.
- Model and configuration versions.
- Proposed weights or risk budgets.
- Expected risk measures.
- Estimated costs.
- Turnover.
- Constraint evaluation.
- Stress results.
- Uncertainty and data quality warnings.
- Comparison with current allocation.
- Comparison with a simple baseline.
- Recommendation status.

**Hard constraints**

Hard constraints must be enforced independently of the optimizer's objective function. If the optimizer returns an infeasible allocation, it must not be silently rounded or passed to execution.

An independent deterministic validator must re-check the final proposal.

### 6.8 P11-STRESS — Portfolio Stress Testing

**Purpose:** Assess how the combined portfolio behaves under adverse conditions.

Scenario families may include:

- Broad equity drawdown.
- Volatility spike.
- Liquidity deterioration.
- Spread widening.
- Correlation convergence.
- Currency shock.
- Interest-rate shock.
- Crypto gap and venue disruption.
- Funding-rate shock.
- Options volatility-surface shock.
- Large underlying move with changing volatility.
- Expiry and assignment scenarios.
- Betting-event loss scenarios.
- Settlement delay.
- Provider outage.
- Multiple simultaneous section losses.

Scenarios must be relevant to the instruments held. Do not apply the same return shock to every section merely for convenience.

Each stress report should include:

- Scenario ID and version.
- Portfolio snapshot ID.
- Assumptions.
- Per-section impact.
- Total portfolio impact.
- Liquidity and liquidation considerations.
- Constraint breaches.
- Model limitations.
- Timestamp.
- Required response, if configured.

Historical stress scenarios do not cover all possible future events. Scenario coverage must be documented as incomplete by design.

### 6.9 P11-REBALANCE — Rebalance Proposal

**Purpose:** Translate a candidate allocation into proposed changes, not directly into orders.

A proposal may include:

- Current target versus proposed target.
- Proposed risk-budget changes.
- Instruments potentially affected.
- Estimated turnover.
- Estimated costs.
- Required capital.
- Expected risk change.
- Binding constraints.
- Reasons for each proposed change.
- Required approvals.
- Expiry time for the proposal.
- Source snapshot ID.

**Requirements**

- Do not act on stale proposals.
- Recompute or revalidate when the portfolio changes materially.
- Do not duplicate a proposal after a retry.
- Use idempotency keys where supported.
- Respect minimum trade sizes and instrument eligibility.
- Do not create orders directly.
- Submit only through the existing authorized pathway after deterministic validation.
- Preserve the proposal and subsequent execution references for audit.

If the proposal expires or its input state becomes stale, mark it invalid and require a new calculation.

### 6.10 P11-EXPLAIN — Decision and Constraint Explanation

Every allocation proposal must explain:

- What changed.
- Why it changed.
- Which inputs mattered.
- Which risk constraints bind.
- Estimated cost and turnover.
- Expected effect on exposure.
- Stress-test implications.
- What uncertainty remains.
- Why the proposal differs from the current allocation.
- Why the proposal differs from a simple baseline.

An LLM may help summarize the evidence, but the underlying numbers and constraint decisions must come from structured records and deterministic calculations.

The explanation layer must not invent a rationale that is unsupported by the recorded inputs.

## 7. Proposed Data Contracts

These are conceptual contracts to guide repository design. Reuse existing AXEL schemas where equivalent contracts already exist.

### 7.1 PortfolioSnapshot

```python
class PortfolioSnapshot:
    snapshot_id: str
    snapshot_version: int
    snapshot_time: datetime
    base_currency: str
    source_refs: list[str]
    section_status: dict
    capital_summary: dict
    position_refs: list[str]
    open_order_refs: list[str]
    commitment_summary: dict
    data_quality_status: str
    reconciliation_status: str
    schema_version: str
```

### 7.2 ExposureSnapshot

```python
class ExposureSnapshot:
    exposure_snapshot_id: str
    portfolio_snapshot_id: str
    normalization_version: str
    instrument_exposures: list[dict]
    section_exposures: dict
    factor_exposures: dict
    currency_exposures: dict
    concentration_metrics: dict
    liquidity_metrics: dict
    uncertainty_flags: list[str]
    created_at: datetime
```

### 7.3 AllocationProposal

```python
class AllocationProposal:
    proposal_id: str
    portfolio_snapshot_id: str
    exposure_snapshot_id: str
    policy_version: str
    optimizer_version: str
    proposed_budgets: dict
    proposed_targets: list[dict]
    estimated_costs: dict
    estimated_turnover: float
    risk_summary: dict
    stress_report_ref: str
    constraint_results: dict
    data_quality_status: str
    approval_state: str
    expires_at: datetime
    created_at: datetime
```

### 7.4 RebalanceProposal

```python
class RebalanceProposal:
    rebalance_id: str
    allocation_proposal_id: str
    portfolio_snapshot_id: str
    changes: list[dict]
    estimated_costs: dict
    required_capital: dict
    preconditions: list[str]
    validation_state: str
    approval_state: str
    expires_at: datetime
    created_at: datetime
```

### 7.5 ReconciliationIssue

```python
class ReconciliationIssue:
    issue_id: str
    source_systems: list[str]
    entity_type: str
    entity_ref: str
    expected_value: dict
    observed_value: dict
    severity: str
    status: str
    detected_at: datetime
    resolution_ref: str | None
```

The implementation must use the repository's established validation framework, identifier conventions, and database design. The schemas above are illustrative, not a mandate to duplicate existing models.

## 8. Capital and Risk Accounting Rules

### 8.1 Distinguish capital from exposure

Capital allocated to a strategy is not necessarily equal to its notional exposure or maximum loss.

Examples include:

- Leveraged forex positions.
- Options with nonlinear payoff profiles.
- Crypto derivatives.
- Betting contracts with a defined stake and payout.
- Open orders that reserve buying power before a position exists.

The system must maintain separate fields and definitions for:

- Equity.
- Available cash.
- Reserved capital.
- Notional exposure.
- Gross exposure.
- Net exposure.
- Margin.
- Maximum modeled loss.
- Stress loss.
- Liquidity-adjusted exposure.

### 8.2 Prevent double counting

The system must define how each source contributes to the consolidated view.

Examples of errors to prevent:

- Counting reserved cash as both available and committed.
- Counting an open order and its resulting position twice after a partial fill.
- Counting the same asset on two data feeds as two independent positions.
- Counting an option's full notional and delta-equivalent exposure as additive exposure without a defined metric.
- Treating unsettled proceeds as immediately available cash when they are not.
- Treating an unresolved betting stake as free capital.

### 8.3 Currency conversion

Every converted value should record:

- Original amount and currency.
- Conversion rate.
- Rate source.
- Rate timestamp.
- Resulting amount and base currency.
- Conversion model version.

If conversion data is stale or missing, flag the result and apply the relevant policy.

## 9. Allocation Policy and Governance

### 9.1 Section readiness

Each section must have an explicit readiness state, such as:

- `DISABLED`
- `RESEARCH_ONLY`
- `PAPER_ONLY`
- `LIVE_ELIGIBLE`
- `HALTED`
- `RECONCILIATION_BLOCKED`

These labels are proposed names; map them to AXEL's actual readiness model.

The portfolio allocator must never promote a section to live eligibility. It must consume readiness decisions from the authorized system.

### 9.2 Hard versus soft constraints

**Hard constraints** must never be traded away for a better objective score. Examples include:

- Capital limits.
- Maximum exposure.
- Leverage and margin limits.
- Mandatory section limits.
- Instrument eligibility.
- Regulatory and provider restrictions.
- Kill-switch and Watchdog state.
- Required reconciliation health.

**Soft objectives** may be optimized within hard constraints. Examples include:

- Expected return.
- Estimated volatility.
- Turnover.
- Transaction costs.
- Concentration preferences.
- Diversification objectives.

The implementation must not represent a hard limit as a penalty that the optimizer can violate if it believes the reward is sufficiently high.

### 9.3 Human approval

Where AXEL's existing policy requires human approval, portfolio proposals must preserve that requirement.

The optimizer must not:

- Approve its own allocation.
- Modify the approval policy.
- Increase its own risk budget.
- Convert a proposal into an order.
- Change the portfolio's maximum permitted risk.

## 10. Database and Event Design

Use existing AXEL storage and migration conventions where possible.

Potential durable records:

- `portfolio_snapshot`
- `portfolio_snapshot_source`
- `exposure_snapshot`
- `risk_budget_proposal`
- `allocation_proposal`
- `allocation_constraint_result`
- `portfolio_stress_run`
- `rebalance_proposal`
- `reconciliation_issue`
- `portfolio_decision_audit`

Potential events:

- `PortfolioSnapshotCreated`
- `PortfolioSnapshotInvalidated`
- `ReconciliationIssueDetected`
- `ReconciliationIssueResolved`
- `ExposureSnapshotCreated`
- `AllocationProposalCreated`
- `AllocationProposalRejected`
- `AllocationProposalExpired`
- `RebalanceProposalCreated`
- `RebalanceProposalAuthorized`
- `RebalanceExecutionCompleted`
- `RebalanceExecutionFailed`
- `PortfolioLimitBreached`

Event names are proposed. Reuse the repository's event conventions rather than introducing duplicate patterns.

**Requirements**

- Event delivery must be idempotent.
- Durable state must not depend exclusively on Redis or an in-memory cache.
- Schema changes must use the existing migration system.
- Every proposal must be traceable to its input snapshot.
- Every executed change must be traceable to the authorization and execution records.
- Reconciliation must remain possible after restart or partial failure.

## 11. Testing Requirements

### 11.1 Unit tests

- Snapshot aggregation.
- Timestamp and freshness checks.
- Currency conversion.
- Exposure normalization.
- Concentration calculations.
- Risk-budget calculations.
- Cost estimates.
- Constraint validation.
- Stress aggregation.
- Proposal expiration.
- Idempotency-key behavior.
- Reconciliation discrepancy classification.

### 11.2 Portfolio invariants

Test that:

- Available capital never includes reserved capital twice.
- Unknown exposure is not treated as zero.
- Disabled sections are not treated as verified zero-exposure sections.
- A missing position feed prevents false claims of a complete portfolio.
- Allocation proposals reference a valid snapshot.
- Hard limits cannot be overridden by an optimizer.
- Infeasible solutions are rejected.
- Stale proposals cannot proceed without revalidation.
- Existing open orders and commitments affect available capital.
- Section-level constraints remain active.

### 11.3 Quantitative tests

- Correlation estimates use the intended windows.
- Exposure calculations match independent reference calculations.
- Options scenarios capture nonlinear changes where required.
- Currency exposure is aggregated correctly across pairs.
- Costs affect candidate rankings where economically relevant.
- Turnover calculations include all proposed changes.
- Stress losses aggregate without double counting.
- Missing observations produce visible warnings.
- Results remain stable within documented tolerances under reproducible inputs.

### 11.4 Integration tests

- All enabled sections contribute to the consolidated snapshot.
- A disabled section remains correctly classified.
- Snapshot generation survives one noncritical provider failure according to policy.
- Critical source failure blocks affected proposals.
- Reconciliation issues flow into allocation eligibility.
- Allocation proposals reach deterministic validation.
- Authorized rebalances use existing execution pathways.
- Actual fills update the portfolio through the established reconciliation process.

### 11.5 Failure-injection tests

Simulate:

- Broker or exchange outage.
- Stale market prices.
- Missing currency conversion.
- Duplicate position events.
- Duplicate fill events.
- Partial fills.
- Unknown order state.
- Failed cancellation.
- Delayed settlement.
- Database interruption.
- Optimizer timeout.
- Infeasible optimizer result.
- Stale allocation proposal.
- Watchdog halt.
- Kill-switch activation.
- Reconciliation mismatch during a rebalance.

In every case, verify that the system does not bypass authorization or invent a safe portfolio state.

### 11.6 Security and permission tests

- Portfolio optimizer cannot write orders directly.
- Portfolio optimizer cannot write fills or positions directly.
- Portfolio optimizer cannot modify risk configuration.
- Portfolio optimizer cannot disable the Watchdog.
- Portfolio optimizer cannot promote a section to live eligibility.
- Portfolio optimizer cannot bypass human approval.
- Rebalance execution requires the existing authorization path.
- Audit events preserve proposal-to-execution lineage.

## 12. Operational Requirements

### 12.1 Observability

Track:

- Snapshot age.
- Per-source data age.
- Snapshot completion rate.
- Reconciliation mismatch count.
- Exposure calculation failures.
- Allocation proposal rejection count.
- Constraint breach count.
- Optimizer runtime and failure rate.
- Cost-model coverage.
- Stress-test completion.
- Proposal expiry count.
- Rebalance execution outcomes.
- Unresolved critical incidents.

### 12.2 Alerts

Configure alerts for:

- Stale critical portfolio data.
- Unreconciled balances or positions.
- Repeated snapshot failures.
- Unexpected exposure changes.
- Hard constraint breaches.
- Watchdog or kill-switch activation.
- Unknown execution states.
- Infeasible allocation output.
- Rebalance execution failures.
- Material divergence between proposed and actual portfolio state.

Alert thresholds must come from approved configuration, not from an LLM-generated recommendation.

### 12.3 Recovery

After a restart or outage:

1. Re-read authoritative account and section state.
2. Reconcile open orders and positions.
3. Rebuild the latest valid portfolio snapshot.
4. Identify unresolved execution states.
5. Invalidate stale allocation and rebalance proposals.
6. Confirm the Watchdog and kill-switch state.
7. Resume proposal generation only when required state-integrity checks pass.

The optimizer must not resume trading activity simply because its process has restarted.

## 13. Implementation Packages

| Package | Deliverable | Dependency |
|---|---|---|
| `P11-STATE` | Versioned consolidated portfolio snapshots | Existing section state and data sources |
| `P11-RECON` | Reconciliation and discrepancy workflow | Snapshot and execution records |
| `P11-NORMALIZE` | Domain-aware exposure representations | Snapshot and instrument metadata |
| `P11-CORRELATION` | Dependence and shared-factor estimates | Historical data and normalized exposures |
| `P11-COST` | Cost and liquidity estimates | Market data and execution records |
| `P11-RISK-BUDGET` | Risk-budget proposal generator | Constraints and exposure model |
| `P11-STRESS` | Scenario engine and reports | Exposure and risk models |
| `P11-OPTIMIZER` | Constrained allocation candidates | Budgets, costs, and stress results |
| `P11-EXPLAIN` | Evidence-based allocation explanations | Proposal and report records |
| `P11-REBALANCE` | Proposal and execution-lineage workflow | Deterministic authorization |
| `P11-QA` | Invariant, integration, and failure tests | All applicable packages |

## 14. Implementation Order

### Stage 1 — Repository and data audit

- Inspect actual section state schemas.
- Inspect order, fill, position, and balance models.
- Identify current reconciliation behavior.
- Identify authoritative source for each data type.
- Document data freshness and timestamp semantics.
- Identify existing risk interfaces.
- Produce a gap analysis before code changes.

### Stage 2 — Consolidated state

- Implement snapshot contracts.
- Add versioning and lineage.
- Implement freshness and completeness checks.
- Add snapshot tests.
- Add reconciliation issue records.

### Stage 3 — Reconciliation and exposure

- Compare internal state with authoritative source records.
- Implement discrepancy classification.
- Implement domain-specific exposure models.
- Add independent calculation tests.
- Add currency and valuation timestamp handling.

### Stage 4 — Risk analytics

- Implement concentration metrics.
- Implement dependence estimation.
- Add cost and liquidity estimates.
- Add stress scenarios.
- Document uncertainty and fallback behavior.

### Stage 5 — Allocation proposals

- Implement transparent baseline allocation policies.
- Implement hard-constraint validation.
- Add optimizer candidates only after baseline tests pass.
- Compare candidates after costs and under stress.
- Generate evidence-backed proposals.

### Stage 6 — Paper rebalance simulation

- Convert proposals into simulated intended changes.
- Simulate partial fills, costs, and stale-state failures.
- Verify that proposals cannot create live orders.
- Test reconciliation after simulated execution.

### Stage 7 — Controlled readiness review

- Run integration and failure-injection suites.
- Verify all section-level controls remain active.
- Verify Watchdog independence.
- Document unresolved limitations.
- Submit evidence for independent review.
- Keep live execution disabled unless separately authorized through the existing promotion process.

## 15. Dependencies and Parallel Work

Phase 11 depends on reliable section-level position, order, capital, and reconciliation interfaces.

The following work can proceed in parallel after the repository audit:

- Portfolio snapshot schema design.
- Domain-specific exposure specification.
- Cost-model coverage audit.
- Stress-scenario catalogue.
- Allocation policy benchmark design.
- Reconciliation failure-test design.

These should not proceed independently where interfaces or source-of-truth definitions conflict. Agree on the snapshot and exposure contracts before integrating the components.

Phase 11 must support sections in different readiness states. It must not assume that all five sections are production-ready.

## 16. Developer Instructions

Before implementation:

1. Inspect the current repository and its existing schemas.
2. Identify authoritative sources for balances, positions, orders, fills, and commitments.
3. Map existing section-level risk controls.
4. Document data freshness and reconciliation guarantees.
5. Identify unsupported exposure conversions.
6. Provide a file-by-file implementation plan.
7. List all assumptions and unresolved design decisions.

During implementation:

- Reuse existing deterministic risk and execution interfaces.
- Do not add direct order submission to the optimizer.
- Do not weaken section-level limits.
- Do not create a second independent safety mechanism that can conflict with the existing Watchdog.
- Do not treat missing data as zero.
- Do not hide reconciliation discrepancies.
- Do not promote paper-only sections to live eligibility.
- Add migrations, tests, and operational documentation with the code.
- Preserve proposal-to-authorization-to-execution lineage.

## 17. Definition of Done

Phase 11 is complete only when all of the following are demonstrated:

- A consolidated, versioned portfolio snapshot is available.
- Each source has freshness and lineage information.
- Reconciliation discrepancies are detected and tracked.
- Exposure normalization is documented and tested.
- Instrument-specific risk characteristics are preserved.
- Correlation and concentration are evaluated.
- Cost and liquidity estimates affect relevant allocation decisions.
- Stress testing covers relevant section-specific scenarios.
- Allocation proposals record inputs, assumptions, constraints, and model versions.
- Hard constraints are independently enforced.
- Optimizer results are compared with simple baselines.
- Stale or invalid proposals are rejected.
- Rebalance simulations cover partial fills and failure conditions.
- Actual execution can occur only through the existing authorized pathway.
- The Watchdog and kill-switch remain independent.
- Disabled and paper-only sections remain correctly classified.
- Portfolio state can be rebuilt and reconciled after restart.
- Operational runbooks and monitoring are available.

## 18. Research References

1. Carroll et al., “Asset Allocation with Correlation: A Composite Trade-off.”  
   https://doi.org/10.1016/j.ejor.2017.04.015

2. Olivares-Nadal and DeMiguel, “A Robust Perspective on Transaction Costs in Portfolio Optimization.”  
   https://doi.org/10.1287/opre.2017.1699

3. Bhardwaj et al., “Almost Exact Risk Budgeting with Return Forecasts for Portfolio Allocation.”  
   https://doi.org/10.1016/j.orl.2023.02.002

4. Paolella et al. (2025), “Risk parity portfolio optimization under heavy-tailed returns and dynamic correlations.”  
   https://onlinelibrary.wiley.com/doi/10.1111/jtsa.12792

These papers inform selected allocation and cost-model design choices. Their results are context-dependent and do not guarantee that a particular AXEL implementation will perform well.

---

**End of Phase 11 specification.**
