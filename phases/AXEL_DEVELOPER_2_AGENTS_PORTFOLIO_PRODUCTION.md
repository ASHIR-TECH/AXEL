# AXEL — Developer 2 Technical Research and Implementation Specification

## Agent Systems, Portfolio Intelligence and Production Engineering

**Project:** AXEL
**Repository:** https://github.com/ASHIR-TECH/AXEL
**Owner:** Developer 2
**Primary responsibility:** Agent architecture, portfolio integration,
execution intelligence, risk governance, production infrastructure
and system certification.

**Target phases:** 3, 4, 5, 6, 10 integration, 11 integration,
12, 13, 14, 15, 16 and 17.

---

# 1. MISSION

Developer 2 owns the operational and integration architecture
that turns AXEL's research and quantitative outputs into a
controlled, observable and recoverable system.

The objective is to build a platform where:

- Multiple asset sections can operate simultaneously.
- Agents can collaborate without obtaining execution authority.
- Portfolio decisions are evaluated centrally.
- Risk controls operate independently of LLMs.
- Execution is deterministic and auditable.
- Failures are detected and contained.
- Research models cannot promote themselves.
- Human approval remains available for sensitive actions.

## 1.1 Core architectural rule

LLM agents propose.

Deterministic services validate and authorize.

Execution services execute authorized instructions.

Independent watchdog services can halt operations.

No LLM agent should possess unrestricted permission to create,
modify or execute live orders.

## 1.2 Trust zones

### Zone A — Agent intelligence

Contains:

- Analysts.
- Expert Panel.
- Section Managers.
- Axel coordinator.
- Research assistant integrations.

Allowed outputs:

- Signals.
- Research reports.
- Proposals.
- Allocation proposals.
- Explanations.
- Evidence references.

Forbidden direct operations:

- Writing executions.
- Writing fills.
- Changing risk limits.
- Changing kill-switch state.
- Direct broker execution.
- Disabling the watchdog.

### Zone B — Deterministic financial core

Contains:

- Risk engine.
- Order validation.
- Exposure checks.
- Portfolio validation.
- Execution authorization.
- Order lifecycle.
- Reconciliation.

This zone must not rely on an LLM to determine whether
an order is allowed.

### Zone C — Independent watchdog

Contains:

- Emergency halt.
- Health checks.
- Exposure monitoring.
- Stale-data monitoring.
- Execution anomaly detection.
- Independent alerting.

The watchdog should use separate permissions and a separate
operational control path.

---

# 2. PHASE 3 — MULTI-AGENT STOCK INTELLIGENCE

## 2.1 Objectives

Complete a structured multi-agent system for stock analysis.

Suggested hierarchy:

    Axel Coordinator
          |
          v
    Section Manager
          |
          v
    Expert Panel
          |
          +---- Fundamental Analyst
          |
          +---- Technical Analyst
          |
          +---- Quantitative Analyst
          |
          +---- Risk Analyst
          |
          +---- Sentiment Analyst
          |
          v
    Structured Proposal
          |
          v
    Deterministic Risk Engine

The exact agent count should remain configurable.

Do not create additional agents merely to increase complexity.

## 2.2 Analyst responsibilities

### Fundamental analyst

Investigates:

- Revenue.
- Earnings.
- Cash flow.
- Debt.
- Profitability.
- Valuation.
- Corporate actions.
- Regulatory filings.
- Dividend sustainability.

### Technical analyst

Investigates:

- Price structure.
- Trend.
- Momentum.
- Volatility.
- Support/resistance.
- Volume.
- Market regime.

### Quantitative analyst

Investigates:

- Statistical signals.
- Model outputs.
- Historical performance.
- Feature importance.
- Confidence calibration.
- Out-of-sample evidence.

### Risk analyst

Investigates:

- Position risk.
- Volatility.
- Drawdown.
- Correlation.
- Liquidity.
- Concentration.
- Event exposure.

### Sentiment analyst

Investigates:

- News.
- Announcements.
- Sentiment.
- Narrative shifts.
- Source credibility.
- Information age.

## 2.3 Evidence-based proposals

Every proposal should contain:

- proposal_id
- instrument_id
- direction
- thesis
- evidence
- source timestamps
- model versions
- analyst outputs
- uncertainty
- invalidation conditions
- suggested time horizon
- expected costs
- risk warnings
- created_at
- expiry_at

A confidence score is not a probability unless calibrated.

## 2.4 Agent disagreement

Agents must be able to disagree.

Do not force consensus by averaging all opinions.

A disagreement should be represented explicitly.

Example:

    fundamental_view = bullish
    technical_view = bearish
    risk_view = high_uncertainty

The Section Manager should explain the conflict
and may reject the proposal.

## 2.5 Research direction

Relevant literature:

- TradingAgents: Multi-Agents LLM Financial Trading Framework.
  https://www.researchgate.net/publication/387540371_TradingAgents_Multi-Agents_LLM_Financial_Trading_Framework

- Agentic Financial Trading Agents: A Comprehensive Literature
  and Research Survey.
  https://www.researchgate.net/publication/389806855_A_multi-agent_System_Based_On_LLM_For_Trading_Financial_Assets

These are architecture and research references.
They do not establish that LLM trading agents are profitable.

## 2.6 Prompt-injection resistance

Treat external news, websites, documents and market messages
as untrusted data.

An analyst must not follow instructions found inside market data.

Use:

- Strict system instructions.
- Structured tool permissions.
- Input provenance.
- Output schemas.
- Context isolation.
- Tool allowlists.
- Output validation.
- Adversarial tests.

---

# 3. PHASE 4 — DASHBOARD AND COMMUNICATIONS

## 3.1 Objectives

The dashboard must display real system state.

It must not display simulated values as if they were actual
positions, fills, P&L or risk metrics.

## 3.2 Required dashboard sections

### Overview

- Total equity.
- Available capital.
- Realised P&L.
- Unrealised P&L.
- Drawdown.
- Exposure.
- System status.

### Asset sections

- Stocks.
- Cryptocurrency.
- Forex.
- Options.
- Betting and prediction markets.

### Agent monitoring

- Agent status.
- Last execution.
- Processing duration.
- Model version.
- Token usage.
- Errors.
- Proposal count.
- Rejection count.

### Risk monitoring

- Current exposure.
- Concentration.
- Volatility.
- VaR.
- Expected Shortfall.
- Drawdown.
- Kill-switch status.
- Limit utilisation.

### Execution monitoring

- Pending orders.
- Accepted orders.
- Rejected orders.
- Partial fills.
- Completed fills.
- Cancelled orders.
- Broker reconciliation.

### Research monitoring

- Active experiments.
- Completed experiments.
- Failed experiments.
- Validation status.
- Model versions.
- Approval state.

## 3.3 Observability equations

Availability:

    Availability = uptime / total_time

Error rate:

    ErrorRate = failed_requests / total_requests

Fill ratio:

    FillRatio = filled_quantity / submitted_quantity

Implementation shortfall:

    IS = execution_cost_relative_to_benchmark

The benchmark price and sign convention must be documented.

## 3.4 Alerts

Alert conditions:

- Data feed stale.
- Broker disconnected.
- Execution rejected unexpectedly.
- Reconciliation mismatch.
- Risk threshold exceeded.
- Watchdog unavailable.
- Database unavailable.
- Model drift detected.
- Agent repeatedly failing.
- Excessive latency.

Alerts must identify severity, time, affected service
and recommended response.

---

# 4. PHASE 5 — PAPER TRADING SOAK

## 4.1 Objectives

Demonstrate that the complete system can operate reliably
before any live promotion.

Paper trading must exercise the same application flow
as live execution wherever practical.

## 4.2 Required metrics

- Paper P&L.
- Maximum drawdown.
- Exposure.
- Order rejection rate.
- Fill ratio.
- Slippage estimate.
- Decision latency.
- Market-data latency.
- Reconciliation failures.
- Agent errors.
- Risk rejections.
- System uptime.

## 4.3 Promotion requirements

The promotion gate should require:

1. Sufficient operating duration.
2. Sufficient decision and execution observations.
3. Stable data ingestion.
4. No unexplained reconciliation mismatch.
5. Successful watchdog drills.
6. Successful kill-switch drills.
7. Verified order lifecycle.
8. Documented risk behaviour.
9. Human review.

A fixed number of trades is not sufficient evidence by itself.

The sample must be interpreted in the context of strategy frequency,
market regime and statistical uncertainty.

## 4.4 Failure drills

Simulate:

- Broker disconnect.
- Data feed disconnect.
- Database interruption.
- Stale prices.
- Duplicate order submission.
- Delayed fill.
- Partial fill.
- Incorrect position.
- Agent failure.
- Watchdog failure.

The system must fail safely.

---

# 5. PHASE 6 — CONTROLLED LIVE EXECUTION

## 5.1 Objectives

Enable tightly controlled live trading only after paper validation
and operational sign-off.

## 5.2 Order lifecycle

    Proposal
       |
       v
    Schema Validation
       |
       v
    Risk Validation
       |
       v
    Approval
       |
       v
    Order Creation
       |
       v
    Broker Submission
       |
       v
    Broker Acknowledgement
       |
       v
    Fill / Partial Fill / Reject
       |
       v
    Reconciliation

Every transition must be recorded.

## 5.3 Almgren-Chriss execution framework

Reference:

Almgren and Chriss (2001),
"Optimal Execution of Portfolio Transactions".

Research publication:
https://www.math.nyu.edu/faculty/chriss/optliq_f.pdf

The classical framework studies the trade-off between
market impact and execution risk.

A simplified objective:

    Minimise:
        E[C] + lambda * Var(C)

Where:

- E[C] = expected execution cost.
- Var(C) = execution-cost variance.
- lambda = risk aversion.

The model provides a theoretical foundation for execution scheduling.

It does not replace broker-specific fill modelling.

## 5.4 Deterministic risk controls

Required controls:

- Maximum order size.
- Maximum position size.
- Maximum asset exposure.
- Maximum portfolio exposure.
- Maximum daily loss.
- Maximum drawdown.
- Maximum leverage.
- Minimum liquidity.
- Maximum spread.
- Maximum order frequency.
- Stale-data rejection.
- Duplicate-order prevention.
- Price collars.
- Emergency halt.

All limits must be configurable through authorised processes.

An LLM must not be able to change them.

## 5.5 Kill-switch

The kill-switch must:

- Stop new order submissions.
- Cancel eligible outstanding orders.
- Record the trigger.
- Notify operators.
- Prevent automatic reactivation.
- Require authorised recovery.

Test the kill-switch during simulated failures.

---

# 6. PHASE 10 — RESEARCH GOVERNANCE INTEGRATION

## 6.1 Objective

Connect Developer 1's research outputs to a controlled
model and strategy promotion process.

## 6.2 Promotion lifecycle

    Research hypothesis
           |
           v
    Experiment registered
           |
           v
    Historical validation
           |
           v
    Out-of-sample validation
           |
           v
    Statistical review
           |
           v
    Code review
           |
           v
    Human approval
           |
           v
    Paper deployment
           |
           v
    Operational evaluation
           |
           v
    Controlled promotion

## 6.3 Research quality gates

Require:

- Dataset hash.
- Model hash.
- Code commit.
- Experiment configuration.
- Training and testing periods.
- Transaction cost assumptions.
- Benchmark.
- Number of trials.
- Walk-forward results.
- Holdout results.
- Robustness tests.
- Statistical corrections.

The research agent cannot approve its own deployment.

## 6.4 Model registry

Maintain:

- Model identifier.
- Version.
- Owner.
- Asset class.
- Training data.
- Feature version.
- Evaluation results.
- Approval state.
- Deployment state.
- Rollback version.
- Last validation.

---

# 7. PHASE 11 — CROSS-ASSET PORTFOLIO INTELLIGENCE

## 7.1 Objectives

Integrate all asset sections into a unified portfolio view.

The system must account for:

- Equities.
- Options.
- Cryptocurrency.
- Forex.
- Betting and prediction-market exposure.
- Cash.
- Financing.
- Currency exposure.
- Correlation.
- Liquidity.
- Costs.
- Stress risk.

Not every exposure can be represented as a conventional
long-only portfolio weight.

For example, options require nonlinear exposure analysis.
Prediction markets require contract-specific payoff and
settlement modelling.

## 7.2 Mean-variance objective

    Maximise:
        w' * mu - (gamma/2) * w' * Sigma * w

Subject to:

    sum(w_i) = 1

    lower_bound_i <= w_i <= upper_bound_i

    Risk(w) <= Risk_limit

Where:

- w = portfolio weights.
- mu = expected returns.
- Sigma = covariance matrix.
- gamma = risk-aversion parameter.

Expected returns are uncertain estimates.

The optimizer must not interpret them as guaranteed outcomes.

## 7.3 Portfolio volatility

    sigma_p = sqrt(w' * Sigma * w)

## 7.4 Risk contributions

    RC_i = w_i * (Sigma*w)_i / sigma_p

The system should reconcile the sum of component risk
contributions with portfolio volatility.

## 7.5 Expected Shortfall

    ES_alpha = E[L | L >= VaR_alpha]

For empirical implementation, sort historical losses
and calculate the average loss in the selected tail,
using a documented quantile convention.

## 7.6 Constraints

Possible constraints:

- Maximum asset allocation.
- Maximum asset-class allocation.
- Maximum currency exposure.
- Maximum sector exposure.
- Maximum leverage.
- Maximum expected shortfall.
- Minimum liquidity.
- Maximum turnover.
- Maximum transaction cost.
- Maximum concentration.
- Maximum correlated exposure.

## 7.7 Proposal-only integration

The portfolio optimizer must generate:

    AllocationProposal

It must not generate executable broker orders.

The deterministic core must independently validate
the proposal against actual positions, current prices,
available capital and risk limits.

## 7.8 Shadow mode

Run the optimizer without executing its proposals.

Compare:

- Proposed allocations.
- Existing portfolio.
- Realised portfolio risk.
- Turnover.
- Estimated transaction costs.
- Stress losses.
- Tracking error.

Shadow mode must not silently transition into live mode.

---

# 8. PHASE 12 — EXECUTION INTELLIGENCE

## 8.1 Objectives

Develop an intelligent execution research system that improves
execution quality while preserving deterministic restrictions.

The system should investigate:

- Order type selection.
- Execution scheduling.
- Market impact.
- Fill probability.
- Queue position.
- Spread conditions.
- Liquidity prediction.
- Venue selection.
- Adaptive participation rates.

## 8.2 Implementation shortfall

Let:

- P0 = decision or arrival price.
- Pexec = execution price.
- Q = executed quantity.

For a buy order, simplified price shortfall:

    IS = Q * (Pexec - P0)

For a sell order, reverse the sign convention.

A full implementation should also account for:

- Unfilled quantity.
- Opportunity cost.
- Fees.
- Market movement.
- Partial execution.

## 8.3 Market impact

A simplified impact model:

    Impact = eta * sigma * sqrt(Q / V)

Where:

- eta = calibrated impact coefficient.
- sigma = volatility.
- Q = order quantity.
- V = market volume.

This is a modelling approximation.

It must be calibrated separately by asset, venue,
order size and market regime.

## 8.4 Reinforcement learning

Potential research applications:

- Adaptive order scheduling.
- Execution policy selection.
- Participation-rate optimisation.
- Venue selection.

Do not initially allow an RL agent to control live execution.

Development stages:

1. Historical simulation.
2. Replay simulation.
3. Stress testing.
4. Shadow execution.
5. Paper execution.
6. Restricted deployment after approval.

RL must not override:

- Price collars.
- Order limits.
- Exposure limits.
- Kill-switch.
- Broker permissions.

## 8.5 Evaluation metrics

- Implementation shortfall.
- Fill ratio.
- Market impact.
- Rejection rate.
- Opportunity cost.
- Completion time.
- Slippage.
- Fees.
- Adverse selection.

Compare the intelligent policy against simple baselines,
such as market execution and time-weighted schedules.

---

# 9. PHASE 13 — ADVANCED RISK INTELLIGENCE

## 9.1 Objectives

Develop an additional risk-intelligence layer that detects
conditions not adequately represented by ordinary historical
volatility and covariance.

Areas:

- Volatility clustering.
- Correlation breakdown.
- Liquidity stress.
- Tail dependence.
- Regime change.
- Counterparty risk.
- Concentration.
- Cross-asset contagion.
- Extreme-event scenarios.

## 9.2 Volatility estimation

EWMA variance:

    sigma_t^2 = lambda*sigma_(t-1)^2
                + (1-lambda)*r_(t-1)^2

Where:

- lambda = decay parameter.
- r = return.

GARCH(1,1):

    sigma_t^2 = omega
                + alpha*epsilon_(t-1)^2
                + beta*sigma_(t-1)^2

Constraints must ensure a valid variance process.

## 9.3 Correlation stress

Historical correlation may not remain stable during a crisis.

Test:

- Historical correlation.
- Rolling correlation.
- Stress correlation.
- Correlation breakdown.
- Common-factor exposure.

Do not assume diversification remains constant
under extreme market conditions.

## 9.4 Stress testing

Define explicit scenarios:

- Equity market crash.
- Interest-rate shock.
- Currency shock.
- Cryptocurrency exchange failure.
- Liquidity collapse.
- Volatility spike.
- Broker outage.
- Stablecoin depeg.
- Prediction-market settlement failure.

For each scenario calculate:

    PortfolioStressLoss =
        PortfolioValue_before - PortfolioValue_after

Scenarios should identify their assumptions and limitations.

## 9.5 Risk alerts

The system should distinguish:

- Warning.
- Elevated risk.
- Limit breach.
- Emergency halt.

The deterministic core remains responsible for enforcing
hard limits.

---

# 10. PHASE 14 — MODEL GOVERNANCE

## 10.1 Objectives

Build a lifecycle management system for every model
used in AXEL.

Required capabilities:

- Model registry.
- Versioning.
- Dataset lineage.
- Feature lineage.
- Experiment history.
- Approval.
- Deployment.
- Monitoring.
- Rollback.
- Retirement.

## 10.2 Model performance monitoring

Monitor:

- Prediction error.
- Calibration.
- Feature drift.
- Concept drift.
- Data drift.
- Latency.
- Failure rate.
- Cost.
- Out-of-sample performance.

## 10.3 Population Stability Index

For a feature divided into bins:

    PSI = sum((A_i - E_i) * ln(A_i / E_i))

Where:

- A_i = actual proportion in bin i.
- E_i = expected or reference proportion in bin i.

PSI is a drift indicator, not a proof that a model
has become unsafe or invalid.

The implementation must handle empty bins using
a documented smoothing procedure.

## 10.4 Governance requirements

Every deployed model needs:

- Identifiable owner.
- Documented purpose.
- Approved asset classes.
- Known limitations.
- Evaluation evidence.
- Rollback procedure.
- Monitoring policy.
- Review schedule.

A model must be deactivated or restricted if its validation
or data assumptions are no longer satisfied.

---

# 11. PHASE 15 — DISTRIBUTED INFRASTRUCTURE

## 11.1 Objectives

Make AXEL capable of operating its asset sections simultaneously
without allowing one failing service to destabilise the entire system.

Previously stated project scale targets:

- Approximately 1,000,000 API calls per day.
- More than 200,000 database entries per day.

These are planning estimates and must be confirmed through
load testing.

## 11.2 Event-driven architecture

Suggested pattern:

    Provider
       |
       v
    Ingestion Service
       |
       v
    Message Broker
       |
       +----> Normalisation
       |
       +----> Feature Engineering
       |
       +----> Analytics
       |
       +----> Agent Services
       |
       +----> Monitoring

Use idempotent event processing.

A repeated event must not create repeated financial actions.

## 11.3 Throughput calculation

Average requests per second:

    RPS = daily_requests / 86400

For one million requests:

    RPS = 1,000,000 / 86,400
        = approximately 11.57

This is only an average.

Capacity planning must consider peak traffic,
provider bursts, retries and concurrent connections.

## 11.4 Reliability patterns

Implement:

- Bounded retries.
- Exponential backoff.
- Circuit breakers.
- Dead-letter queues.
- Idempotency keys.
- Backpressure.
- Rate-limit management.
- Health checks.
- Graceful shutdown.
- Replay.
- Recovery.

Never retry an order-submission request blindly.

An uncertain broker response must first be reconciled
against broker order state.

## 11.5 Database requirements

Use:

- Indexed queries.
- Connection pooling.
- Batched writes.
- Partitioning where appropriate.
- Migration discipline.
- Backup verification.
- Replication monitoring.
- Retention policies.
- Query performance monitoring.

The database should not be treated as a single universal
store for every workload.

Separate transactional, analytical, streaming and raw-data
responsibilities where justified by measured performance.

## 11.6 Failure isolation

A failing cryptocurrency adapter must not stop
the stock-data service.

A failed LLM provider must not disable the deterministic
risk engine.

A failed dashboard must not disable the watchdog.

A failed analytics service must not create orders.

---

# 12. PHASE 16 — SECURITY AND COMPLIANCE

## 12.1 Objectives

Protect the platform from:

- Unauthorised execution.
- Credential leakage.
- Prompt injection.
- Data tampering.
- Privilege escalation.
- Insider misuse.
- API abuse.
- Dependency compromise.
- Unauthorised configuration changes.

## 12.2 Least privilege

Separate permissions for:

- Agent services.
- Market-data services.
- Research services.
- Risk engine.
- Execution engine.
- Watchdog.
- Dashboard.
- Database administration.

Agent services should not possess broker execution credentials.

## 12.3 Prompt injection

Treat all external text as untrusted.

This includes:

- News.
- Social posts.
- Research documents.
- Websites.
- Provider metadata.
- Uploaded documents.

Never let external text redefine system instructions.

Use:

- Tool allowlists.
- Structured schemas.
- Input isolation.
- Permission checks.
- Output validation.
- Security logging.
- Adversarial tests.

## 12.4 Audit trail

Record:

- User actions.
- Agent decisions.
- Proposal changes.
- Risk decisions.
- Approval actions.
- Configuration changes.
- Order lifecycle.
- Watchdog actions.
- Model promotions.

Logs should be tamper-resistant and have documented retention.

## 12.5 Secrets

Never place secrets in:

- Git.
- Logs.
- Agent prompts.
- Research reports.
- Dashboard output.
- Unrestricted environment dumps.

Use a dedicated secrets-management mechanism.

Rotate credentials after suspected exposure.

## 12.6 Compliance

Legal requirements depend on the operating jurisdictions,
the asset classes and whether AXEL is used for proprietary
trading, customer activity or regulated services.

Obtain appropriate legal review before enabling
activities that require authorisation.

---

# 13. PHASE 17 — INTEGRATION AND PRODUCTION CERTIFICATION

## 13.1 Objective

Demonstrate that AXEL operates as one coherent system.

This phase is primarily about evidence, not adding features.

## 13.2 End-to-end test

Validate:

    Data ingestion
         |
         v
    Normalisation
         |
         v
    Feature generation
         |
         v
    Analyst agents
         |
         v
    Expert panel
         |
         v
    Section manager
         |
         v
    Axel coordinator
         |
         v
    Proposal
         |
         v
    Deterministic risk
         |
         v
    Approval
         |
         v
    Execution
         |
         v
    Reconciliation
         |
         v
    Dashboard and audit

## 13.3 Required failure tests

### Data failures

- Missing data.
- Stale data.
- Incorrect timestamp.
- Duplicate event.
- Corrupted event.
- Provider outage.

### Agent failures

- Agent timeout.
- Invalid schema.
- Prompt injection.
- Contradictory proposals.
- Hallucinated evidence.
- Excessive resource consumption.

### Risk failures

- Exposure breach.
- Daily loss breach.
- Concentration breach.
- Invalid portfolio snapshot.
- Missing market price.
- Stale risk calculation.

### Execution failures

- Broker timeout.
- Duplicate submission.
- Partial fill.
- Delayed acknowledgement.
- Rejected order.
- Reconciliation mismatch.

### Infrastructure failures

- Database outage.
- Queue outage.
- Network partition.
- Service restart.
- Disk exhaustion.
- Clock drift.

## 13.4 Recovery tests

Verify:

- Recovery after restart.
- Event replay.
- Order-state reconciliation.
- Position reconstruction.
- Database recovery.
- Configuration restoration.
- Model rollback.
- Watchdog recovery.

Never reconstruct a live position exclusively
from agent memory or cached dashboard state.

## 13.5 Production certification gates

Require all of the following:

1. Code review.
2. Automated tests.
3. Integration tests.
4. Security review.
5. Data-quality evidence.
6. Risk-control evidence.
7. Recovery evidence.
8. Paper-trading evidence.
9. Operational runbooks.
10. Human sign-off.

Passing the software tests alone does not establish
production readiness.

---

# 14. SHARED CONTRACTS WITH DEVELOPER 1

Developer 2 consumes:

- Instrument metadata.
- Market-data events.
- Feature records.
- Signal records.
- Quantitative forecasts.
- Experiment reports.
- Portfolio risk inputs.

Developer 2 owns:

- Allocation proposals.
- Risk decisions.
- Execution authorisation.
- Order lifecycle.
- Portfolio snapshots.
- Operational state.
- Model promotion decisions.

Any shared schema change requires joint review.

No developer should independently change the semantics
of a financial field without updating the contract.

---

# 15. TESTING REQUIREMENTS

## Unit testing

Test:

- Risk calculations.
- Order validation.
- Permission checks.
- Exposure limits.
- Portfolio constraints.
- State transitions.
- Alert conditions.

## Integration testing

Test:

- Agent-to-proposal flow.
- Proposal-to-risk flow.
- Risk-to-execution flow.
- Broker integration.
- Reconciliation.
- Watchdog independence.
- Dashboard data accuracy.

## Security testing

Test:

- Privilege escalation.
- Prompt injection.
- Credential leakage.
- Unauthorised order submission.
- Unauthorised configuration changes.
- Replay attacks.
- Duplicate requests.

## Resilience testing

Test:

- Provider failures.
- Queue failures.
- Database failures.
- Network interruptions.
- Restart recovery.
- Partial outages.

## Performance testing

Measure:

- Ingestion throughput.
- Processing latency.
- Queue lag.
- Database write latency.
- Database query latency.
- Agent response time.
- Risk calculation time.
- Order acknowledgement time.

---

# 16. DEVELOPER 2 DELIVERABLES

1. Complete agent hierarchy.
2. Proposal schemas.
3. Agent permission boundaries.
4. Dashboard and communications.
5. Paper-soak monitoring.
6. Controlled live execution gates.
7. Research governance integration.
8. Portfolio intelligence integration.
9. Execution intelligence.
10. Advanced risk intelligence.
11. Model governance.
12. Distributed infrastructure.
13. Security and compliance controls.
14. End-to-end certification.
15. Operational runbooks.
16. Integration and resilience tests.

---

# 17. EXIT CRITERIA

The workstream is complete only when:

- Agents cannot bypass risk controls.
- Dashboard metrics represent actual system state.
- Paper-trading evidence is available.
- Live execution is explicitly gated.
- Portfolio proposals are independently validated.
- Research promotions require approval.
- Watchdog operation is independent.
- Recovery is tested.
- Security boundaries are tested.
- Cross-asset reconciliation works.
- Production certification is documented.

A merged PR is not sufficient evidence of operational readiness.

---

# 18. RESEARCH REFERENCES

These references form a starting bibliography.
Verify full publication metadata, publication status and
methodology before adopting results.

## Multi-agent and AI systems

- TradingAgents: Multi-Agents LLM Financial Trading Framework.
  https://www.researchgate.net/publication/387540371_TradingAgents_Multi-Agents_LLM_Financial_Trading_Framework

- Agentic Financial Trading Agents: A Comprehensive Literature
  and Research Survey.
  https://www.researchgate.net/publication/389806855_A_multi-agent_System_Based_On_LLM_For_Trading_Financial_Assets

## Execution

- Almgren and Chriss (2001), Optimal Execution of Portfolio Transactions.
  https://www.math.nyu.edu/faculty/chriss/optliq_f.pdf

- Kato (2017), An Optimal Execution Problem in the
  Volume-Dependent Almgren-Chriss Model.
  https://doi.org/10.3233/AF-170198

## Portfolio optimisation

- Markowitz (1952), Portfolio Selection.
  https://doi.org/10.1111/j.1540-6261.1952.tb01525.x

- Rockafellar and Uryasev (2000), Optimization of Conditional
  Value-at-Risk.
  https://doi.org/10.21314/JOR.2000.038

## Risk modelling

- Engle (1982), Autoregressive Conditional Heteroscedasticity
  with Estimates of the Variance of United Kingdom Inflation.
  https://doi.org/10.2307/1912773

- Bollerslev (1986), Generalized Autoregressive Conditional
  Heteroskedasticity.
  https://doi.org/10.1016/0304-4076(86)90063-1

## Backtest and model governance

- Bailey et al. (2017), The Probability of Backtest Overfitting.
  https://doi.org/10.21314/JCF.2016.322

- López de Prado, Advances in Financial Machine Learning.
  https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086

## Security and AI governance

- NIST AI Risk Management Framework.
  https://www.nist.gov/itl/ai-risk-management-framework

- OWASP Top 10 for Large Language Model Applications.
  https://owasp.org/www-project-top-10-for-large-language-model-applications/

---

# 19. FINAL IMPLEMENTATION DIRECTIVE

AXEL must remain a deterministic financial system with
AI-assisted research and decision support.

The agent architecture must never become the source of truth
for orders, fills, positions, risk limits or portfolio state.

All financial actions must be independently validated.

Every model must have measurable evidence.

Every important operation must be auditable.

Every critical service must have a tested recovery procedure.

The objective is not to build the largest possible AI trading
architecture. It is to build a system whose decisions,
risks, data and operational behaviour can be independently
verified.
