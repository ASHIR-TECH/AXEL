# AXEL Phase 11 — Cross-Asset Portfolio Intelligence and Capital Allocation

**Document type:** Product Requirements Document (PRD), Quantitative Research Specification, Mathematical Reference, and Software Implementation Plan  
**Phase:** 11  
**Status:** Proposed — repository audit and architecture approval required  
**Primary objective:** Build a reproducible, risk-aware, cost-aware portfolio allocation service that aggregates Stocks, Options, Crypto, Forex, and Bets/Prediction Markets into a common portfolio view and generates proposals for the existing deterministic AXEL trading core.

**Intended implementation environment:** Existing AXEL repository, Python quantitative services, PostgreSQL/TimescaleDB system of record, existing event infrastructure, deterministic risk/execution core, and independent Watchdog. Exact technologies and file paths must be confirmed through a repository audit.

---

# Table of contents

1. Mission and scope
2. Research principles and evidence
3. Architecture and trust boundaries
4. Mathematical notation and conventions
5. Step 0 — Repository audit
6. Step 1 — Canonical portfolio state and reconciliation
7. Step 2 — Instrument and risk-factor registry
8. Step 3 — Cross-asset exposure normalization
9. Step 4 — Returns, covariance, and factor estimation
10. Step 5 — Portfolio risk measures
11. Step 6 — Risk budgeting
12. Step 7 — Portfolio optimization
13. Step 8 — Transaction costs and liquidity
14. Step 9 — Options and nonlinear exposure
15. Step 10 — FX, crypto, and prediction-market exposure
16. Step 11 — Stress testing and robust optimization
17. Step 12 — Allocation proposals and deterministic authorization
18. Step 13 — Data storage, event processing, and audit
19. Step 14 — Testing and quantitative validation
20. Step 15 — Shadow mode and release gates
21. Software engineering standards
22. Module breakdown and interfaces
23. Security, reliability, and operational controls
24. Acceptance criteria
25. Implementation sequence for OpenCode
26. Research library, papers, PDFs, and technical documentation
27. Mathematical formula index
28. Final engineering directive

---

# 1. Mission and scope

## 1.1 Mission

Phase 11 is the portfolio-level intelligence layer of AXEL.

The five trading sections may generate individually attractive signals while collectively producing unacceptable concentration, leverage, liquidity, event, or counterparty risk. Phase 11 must determine how these opportunities interact at the total-portfolio level.

It answers:

> Given the reconciled portfolio, outstanding orders, available capital, existing commitments, expected returns, risk estimates, transaction costs, liquidity, and uncertainty, what allocation should AXEL propose, subject to the approved global constraints?

The service must distinguish between:

- Capital available to deploy.
- Capital already committed.
- Market value of current holdings.
- Gross exposure.
- Net exposure.
- Margin and collateral.
- Maximum possible loss.
- Estimated ordinary risk.
- Stressed risk.
- Liquidity and liquidation horizon.
- Model uncertainty.
- Operational and counterparty risk.

These quantities are not interchangeable.

## 1.2 Supported sections

| Section | Typical exposures | Special considerations |
|---|---|---|
| Stocks | Equity value, sectors, factors, currencies | Corporate actions, gaps, shorting, borrow |
| Options | Delta, gamma, vega, theta, nonlinear payoff | Expiry, implied volatility, assignment, margin |
| Crypto | Spot, derivatives, funding, collateral | Venue, custody, liquidation, 24/7 trading |
| Forex | Currency pairs and currency legs | Leverage, financing, rollover, market sessions |
| Bets/Predictions | Stakes, payouts, event outcomes | Discrete payoffs, event dependencies, settlement |

The common portfolio layer must preserve each section's unique economic and operational characteristics.

## 1.3 Goals

Phase 11 must:

1. Build a timestamped and reconciled portfolio snapshot.
2. Account for current positions, open orders, pending fills, and commitments.
3. Normalize instruments into common economic exposures.
4. Estimate risk and shared-factor concentration.
5. Enforce global and section risk budgets.
6. Generate feasible candidate allocations.
7. Account for transaction costs and liquidity.
8. Stress-test the complete portfolio.
9. Explain and persist every proposal.
10. Support deterministic replay and audit.
11. Compare sophisticated methods against simple baselines.
12. Fail safely when mandatory inputs are missing or invalid.

## 1.4 Non-goals

Phase 11 must not:

- Submit orders directly.
- Modify approved risk limits.
- Override the deterministic trading core.
- Disable or replace the independent Watchdog.
- Treat a model's expected return as guaranteed.
- Infer missing balances as zero.
- Assume that historical correlations remain stable in crises.
- Promote itself into live trading.
- Merge its own changes without the established review process.
- Treat a successful optimizer status as proof of financial safety.
- Claim that a particular allocation method guarantees profitability.

## 1.5 Definition of success

Phase 11 succeeds when it produces auditable, feasible, reproducible proposals and demonstrates through quantitative and operational testing that the system obeys its constraints.

A profitable backtest is neither necessary to establish software correctness nor sufficient to establish production readiness.

---

# 2. Research principles and evidence

This section distinguishes published research from AXEL-specific engineering decisions.

## 2.1 Robust optimization and estimation uncertainty

**Reference:** Yin, Perchet and Soupé, *A Practical Guide to Robust Portfolio Optimization*.

- Publisher: https://doi.org/10.1080/14697688.2020.1849780
- Research context: uncertainty in portfolio inputs and robust allocation.

**Design consequence:** Avoid unconstrained optimization based on fragile expected-return estimates. Add bounds, regularization, sensitivity analysis, and robustness tests.

## 2.2 Covariance shrinkage

**Reference:** Ledoit and Wolf, *Honey, I Shrunk the Sample Covariance Matrix*.

- Direct PDF: https://www.ledoit.net/honey.pdf
- Publisher record: https://doi.org/10.3905/jpm.2004.110
- Additional academic page: https://bse.eu/research/working-papers/honey-i-shrunk-sample-covariance-matrix

**Design consequence:** A raw sample covariance matrix can be unstable when the number of assets is large relative to the number of observations. Implement and evaluate shrinkage estimators.

## 2.3 Multi-portfolio risk budgeting

**Reference:** *Risk-budgeting multi-portfolio optimization with portfolio and marginal risk constraints*.

- ResearchGate: https://www.researchgate.net/publication/283557924_Risk-budgeting_multi-portfolio_optimization_with_portfolio_and_marginal_risk_constraints

**Design consequence:** Section-level budgets must reconcile with portfolio-level risk limits. Risk must be aggregated before the system distributes capacity among sections.

## 2.4 Transaction costs and allocation stability

**Reference:** *A Robust Perspective on Transaction Costs in Portfolio Optimization*.

- ResearchGate: https://www.researchgate.net/publication/286443114_A_Robust_Perspective_on_Transaction_Costs_in_Portfolio_Optimization

**Design consequence:** Penalize turnover, estimate implementation costs, and evaluate net performance. The optimizer must not trade repeatedly because small estimation changes produce unstable weights.

## 2.5 Error amplification in portfolio optimization

**Reference:** *Portfolio Construction by Mitigating Error Amplification: The Bounded-Noise Portfolio*.

- Publisher: https://doi.org/10.1287/opre.2019.1858

**Design consequence:** Monitor how small input changes affect allocation weights. Reject methods that produce unstable allocations without demonstrable out-of-sample benefits.

## 2.6 Expected shortfall and CVaR

**Reference:** Rockafellar and Uryasev, *Conditional Value-at-Risk for General Loss Distributions*.

- Publisher: https://doi.org/10.1016/S0378-4266(02)00271-6
- Researcher publication page: https://uryasev.github.io/publications/

**Design consequence:** Implement a tail-loss metric such as expected shortfall alongside volatility and drawdown. Scenario-based CVaR can be optimized using a convex formulation under suitable assumptions.

## 2.7 Backtest overfitting and selection bias

**Reference:** Bailey, Borwein, López de Prado and Zhu, *The Probability of Backtest Overfitting*.

- Publisher: https://doi.org/10.21314/JCF.2016.322
- Open academic record with PDF access: https://escholarship.org/uc/item/4w1110bb

**Design consequence:** Track the number of experiments performed. Use chronological validation, untouched holdouts, and methods that account for strategy-selection bias.

## 2.8 Deflated Sharpe Ratio

**Reference:** Bailey and López de Prado, *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality*.

- Publisher: https://doi.org/10.3905/jpm.2014.40.5.094
- Direct PDF: https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf

**Design consequence:** Do not select an optimizer solely because it achieved the highest Sharpe ratio among many trials.

## 2.9 Automated-trading controls

**Reference:** Futures Industry Association, *Best Practices for Automated Trading Risk Controls and System Safeguards*.

- Direct PDF: https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf
- FIA article: https://www.fia.org/fia/articles/fia-releases-best-practices-automated-trading-risk-controls-and-system-safeguards

**Design consequence:** Keep controls independent of strategy logic, verify proposals before execution, monitor production behavior, and test failure handling.

These references inform design decisions but do not prove that one model will be best for AXEL. Each method still requires validation using AXEL's own data, costs, products, and constraints.

---

# 3. Architecture and trust boundaries

## 3.1 Required architecture

```text
Stocks / Options / Crypto / Forex / Bets agents
                       |
                       v
               Validated signals
                       |
                       v
            Portfolio state service
                       |
                       v
           Reconciliation and registry
                       |
                       v
            Exposure normalization
                       |
                       v
            Portfolio risk engine
                       |
                       v
              Risk budget engine
                       |
                       v
           Candidate allocation engine
                       |
                       v
             Cost and stress checks
                       |
                       v
          Allocation proposal + audit
                       |
                       v
          Deterministic trading core
                       |
             Accept / Reject / Defer
                       |
                       v
            Existing execution service
                       |
                       v
            Fills and reconciliation
```

Phase 11 does not become an execution service. The deterministic core remains the final authority.

## 3.2 Trust domains

| Domain | Responsibility | Must not do |
|---|---|---|
| Section agents | Propose opportunities | Override portfolio limits |
| Phase 11 | Aggregate, measure, optimize, explain | Write execution orders directly |
| Deterministic core | Validate and authorize | Delegate authorization to an LLM |
| Watchdog | Independent detection and halt behavior | Depend on Phase 11 for safety |
| Human reviewer | Review governed changes | Bypass mandatory checks through undocumented exceptions |

**Invariant:** Phase 11 must not write authoritative orders, fills, positions, or risk configuration except through a specifically approved existing interface with narrowly defined permissions.

## 3.3 Data authority

Every datum must have an explicit authority level:

1. External market or venue source.
2. Ingested event.
3. Validated internal observation.
4. Reconciled portfolio state.
5. Calculated risk or optimization output.
6. Proposed action.
7. Deterministically authorized action.
8. Confirmed execution and reconciled result.

A proposal must never be treated as evidence that an order was executed.

---

# 4. Mathematical notation and conventions

Use consistent notation throughout the codebase and documentation.

| Symbol | Meaning |
|---|---|
| \(i,j\) | Instrument indices |
| \(t\) | Time index |
| \(n\) | Number of instruments |
| \(m\) | Number of observations |
| \(r_{i,t}\) | Return of instrument \(i\) at time \(t\) |
| \(w_i\) | Portfolio weight or normalized exposure |
| \(w\) | Portfolio allocation vector |
| \(w_0\) | Current allocation vector |
| \(\mu\) | Expected-return vector |
| \(\Sigma\) | Return covariance matrix |
| \(B\) | Factor exposure matrix |
| \(f_t\) | Factor return vector |
| \(V\) | Portfolio value or equity, as explicitly defined |
| \(C(w,w_0)\) | Cost of moving from current to proposed portfolio |
| \(L\) | Portfolio loss, with positive values representing losses |
| \(\alpha\) | Tail probability level, such as 0.05 |
| \(R\) | Return or reward, according to context |
| \(A\) | Approved capital or risk budget, according to context |

## 4.1 Unit conventions

Every formula must specify whether values are:

- Fractions or percentages.
- Simple or logarithmic returns.
- Daily, hourly, annualized, or event-horizon values.
- Base-currency or local-currency values.
- Per-contract, per-unit, per-share, or total-position values.
- Gross or net of costs.
- Before or after financing and fees.

Never combine quantities with incompatible units.

## 4.2 Time conventions

- Use UTC internally for event timestamps.
- Preserve the source's original timestamp and timezone.
- Store exchange-local trading calendars where relevant.
- Distinguish event time from ingestion time.
- Do not combine daily observations from different close times as though they were synchronous.
- Crypto and Forex operate on different calendars from listed equities; annualization factors must be explicitly defined.

---

# 5. Step 0 — Repository audit

**Goal:** Map the specification to the actual AXEL repository before implementation.

## 5.1 Tasks

1. Inspect repository files, branches, migrations, tests, CI, and existing PRDs.
2. Read the current deterministic risk and execution contracts.
3. Identify existing position, order, fill, account, and balance records.
4. Identify the existing Watchdog API and authority model.
5. Run the existing test suite and preserve its baseline.
6. Map current services to the architecture in Section 3.
7. Identify which requirements are already implemented.
8. Identify missing interfaces and data-quality assumptions.
9. Produce architecture decision records for material choices.
10. Produce an implementation plan with exact existing file paths.

## 5.2 Deliverables

- Repository audit.
- Current-state architecture diagram.
- Schema and API inventory.
- Test baseline.
- Gap analysis.
- Migration plan.
- List of unresolved decisions.

## 5.3 Exit criteria

Do not begin major implementation until the developer can explain how Phase 11 integrates with the existing system without duplicating or bypassing core safety controls.

---

# 6. Step 1 — Canonical portfolio state and reconciliation

**Goal:** Build a trustworthy record of holdings, obligations, and possible exposures.

## 6.1 Required entities

- Account.
- Venue or custodian.
- Instrument.
- Position.
- Open order.
- Pending fill.
- Cash balance.
- Currency.
- Reserved capital.
- Margin and collateral.
- Fee and funding obligation.
- Settlement record.
- Corporate action.
- Source observation.
- Reconciliation issue.
- Portfolio snapshot.

## 6.2 Capital accounting

Do not use one ambiguous `capital` field for all purposes.

At minimum distinguish:

- `cash_balance`: reconciled cash in a specific currency.
- `equity_value`: value of holdings under a documented valuation method.
- `account_equity`: equity according to the authoritative account definition.
- `reserved_order_capacity`: capacity reserved for outstanding orders.
- `margin_requirement`: required margin.
- `available_to_trade`: authoritative venue value where available.
- `risk_budget_remaining`: approved risk capacity, not cash.
- `liquidation_value_estimate`: modeled proceeds under specified costs and market assumptions.

For a simple fully reconciled cash account, a simplified equity identity may be:

\[
E_t = C_t + \sum_i q_{i,t} P_{i,t} + A_t - L_t
\]

where:

- \(C_t\) is cash converted to the reporting currency.
- \(q_{i,t}\) is signed position quantity.
- \(P_{i,t}\) is the validated price.
- \(A_t\) is other recognized assets.
- \(L_t\) is recognized liabilities.

The exact identity depends on the venue, instrument, and accounting convention. Derivatives, margin accounts, unsettled trades, and prediction contracts require product-specific accounting. Do not double-count derivative notional as cash or equity.

## 6.3 Position valuation

For a linear instrument with signed quantity \(q_i\), multiplier \(M_i\), and price \(P_i\):

\[
MV_i = q_i M_i P_i
\]

Convert into reporting currency using a validated FX conversion \(X_{c \rightarrow b}\):

\[
MV_i^{(b)} = MV_i^{(c)} X_{c \rightarrow b}
\]

For non-linear derivatives, this market-value calculation is not sufficient to represent risk.

## 6.4 Outstanding-order exposure

For an open order, the possible incremental position depends on side, remaining quantity, price, order type, and fill uncertainty.

For a simple linear instrument:

\[
\Delta MV_{\max} \approx |q_{\mathrm{remaining}}| M P_{\mathrm{risk}}
\]

where \(P_{\mathrm{risk}}\) is a conservative reference price appropriate to the risk policy.

This approximation must not be used as a universal options or event-contract risk formula. Use product-specific maximum loss, margin, or scenario exposure where required.

A proposal must consider the combined portfolio under relevant fill states. Avoid double-counting an order that has already been included in a reconciled position.

## 6.5 Reconciliation invariants

1. Missing balances are unknown, not zero.
2. Duplicate events must not duplicate exposure.
3. Every position has a stable instrument ID.
4. Every value has a source timestamp and currency.
5. Pending orders and fills are represented explicitly.
6. Unsettled transactions are not silently treated as settled.
7. Material discrepancies create a reconciliation issue.
8. Corrections preserve an audit trail.
9. Inconsistent snapshots cannot be used for risk-increasing proposals.
10. An API timeout must never be interpreted as evidence that no position exists.

## 6.6 Idempotency

For an event-processing operation, the same logical event must not apply twice.

A simple idempotency key can be defined as:

\[
K = H(\text{source},\text{account},\text{event ID},\text{event version})
\]

where \(H\) is a cryptographic hash or a deterministic composite identifier. A hash alone is not a substitute for database uniqueness constraints.

## 6.7 Exit criteria

- Event replay reconstructs the same logical state.
- Duplicate delivery does not duplicate holdings.
- Missing data is surfaced.
- Reconciliation errors are queryable.
- Position and capital accounting identities are tested.

---

# 7. Step 2 — Instrument and risk-factor registry

**Goal:** Define what each instrument is and which economic risks it creates.

## 7.1 Required instrument fields

- Stable internal ID.
- Asset class.
- Instrument type.
- Underlying.
- Issuer or event.
- Venue and counterparty.
- Trading currency.
- Settlement currency.
- Contract multiplier.
- Tick size.
- Lot size.
- Expiration.
- Exercise style.
- Settlement style.
- Trading calendar.
- Price source.
- Liquidity classification.
- Valuation method.
- Risk model version.
- Eligibility status.

## 7.2 Common risk factors

Where applicable, define factors for:

- Broad equity markets.
- Sector and industry.
- Equity style factors.
- Interest-rate curves.
- FX currencies.
- Commodities.
- Crypto markets.
- Volatility surfaces.
- Credit and counterparty risk.
- Funding and borrow costs.
- Event outcomes.
- Custody and venue concentration.

## 7.3 Factor model

Represent returns as:

\[
r_t = B f_t + \epsilon_t
\]

where:

- \(r_t\) is the vector of instrument returns.
- \(B\) is the factor loading matrix.
- \(f_t\) is the vector of factor returns.
- \(\epsilon_t\) is the residual vector.

If factor and residual returns are uncorrelated under the model assumptions:

\[
\Sigma_r = B\Sigma_f B^\top + \Sigma_\epsilon
\]

Do not assume residual independence without testing it.

## 7.4 Exit criteria

Every supported instrument has a validated mapping. Unsupported instruments are explicitly excluded or blocked; they must not silently disappear from portfolio risk.

---

# 8. Step 3 — Cross-asset exposure normalization

**Goal:** Express exposures consistently without pretending that all instruments have the same payoff structure.

## 8.1 Linear instruments

For an instrument with quantity \(q_i\), multiplier \(M_i\), and price \(P_i\), market value is:

\[
MV_i = q_i M_i P_i
\]

For a reporting currency \(b\):

\[
MV_i^{(b)} = MV_i^{(c)} X_{c\rightarrow b}
\]

The signed value is useful for net exposure. Gross exposure generally uses absolute values.

\[
G = \sum_i |MV_i|
\]

\[
N = \sum_i MV_i
\]

These definitions are meaningful only after deciding which instruments and accounting values are included. Gross and net notional may be more appropriate than market value for some derivatives.

## 8.2 Weights

For portfolio equity \(E>0\):

\[
w_i = \frac{MV_i}{E}
\]

Gross exposure ratio:

\[
L_{\mathrm{gross}} = \frac{\sum_i |MV_i|}{E}
\]

Net exposure ratio:

\[
L_{\mathrm{net}} = \frac{\sum_i MV_i}{E}
\]

These are exposure ratios, not universal measures of leverage. Derivatives can have low market value and high economic exposure.

## 8.3 Stocks

For equities, track at least:

- Market value.
- Sector.
- Issuer.
- Country.
- Currency.
- Market beta.
- Factor exposures.
- Borrow availability for shorts.
- Corporate-action state.

## 8.4 Options

Do not equate option premium with maximum exposure.

Track:

- Premium market value.
- Underlying exposure.
- Delta-equivalent exposure.
- Gamma and vega.
- Maximum loss when defined.
- Margin requirement.
- Expiry and settlement.
- Full-revaluation scenario P&L.

Delta-equivalent exposure is a local sensitivity measure, not a guarantee of payoff under large price moves.

## 8.5 Crypto

Track:

- Spot and derivative positions.
- Venue and custody.
- Collateral.
- Funding and borrow.
- Liquidation price where relevant.
- Exchange concentration.
- Asset-transfer dependencies.
- Market-hours and data freshness.

A position held at one venue must not automatically be considered available at another venue.

## 8.6 Forex

Represent currency legs explicitly. For a base/quote pair, a long position typically represents long base currency and short quote currency, subject to the broker's contract definition.

Currency exposure must be aggregated by currency, not merely by pair label.

## 8.7 Bets and prediction contracts

For each contract, store:

- Event ID.
- Outcome ID.
- Stake.
- Entry price or odds.
- Maximum possible loss.
- Payout schedule.
- Settlement rules.
- Event dependencies.
- Platform and counterparty.
- Settlement status.

For mutually exclusive outcomes, model the outcome payoff matrix rather than applying ordinary asset-return covariance mechanically.

---

# 9. Step 4 — Returns, covariance, and factor estimation

**Goal:** Estimate the distribution and co-movement of portfolio outcomes using point-in-time data.

## 9.1 Simple returns

For price \(P_t\):

\[
r_t = \frac{P_t}{P_{t-1}} - 1
\]

## 9.2 Log returns

\[
\ell_t = \ln\left(\frac{P_t}{P_{t-1}}\right)
\]

Log returns aggregate conveniently across time under their assumptions, but portfolio P&L and weights must be calculated using economically appropriate returns. Do not substitute log returns into every formula without checking the model.

## 9.3 Arithmetic mean

For \(T\) observations:

\[
\bar r_i = \frac{1}{T}\sum_{t=1}^{T}r_{i,t}
\]

This is a historical average, not necessarily an unbiased forecast of future expected return under changing market conditions.

## 9.4 Sample variance and covariance

\[
s_i^2 = \frac{1}{T-1}\sum_{t=1}^{T}(r_{i,t}-\bar r_i)^2
\]

\[
s_{ij} =
\frac{1}{T-1}
\sum_{t=1}^{T}
(r_{i,t}-\bar r_i)(r_{j,t}-\bar r_j)
\]

The sample covariance matrix \(S\) consists of all \(s_{ij}\).

## 9.5 Correlation

\[
\rho_{ij} = \frac{s_{ij}}{s_i s_j}
\]

Correlation is undefined or unstable when a variance is zero or nearly zero. Such cases require explicit handling.

## 9.6 Portfolio variance

For weights \(w\) and covariance matrix \(\Sigma\):

\[
\sigma_p^2 = w^\top \Sigma w
\]

Portfolio volatility:

\[
\sigma_p = \sqrt{w^\top \Sigma w}
\]

This formula assumes a coherent return vector and covariance estimate. For nonlinear instruments, it may be an approximation to actual portfolio risk.

## 9.7 Annualization

If returns are sampled at a consistent frequency with \(k\) periods per year:

\[
\mu_{\mathrm{annual}} \approx k\mu_{\mathrm{period}}
\]

\[
\Sigma_{\mathrm{annual}} \approx k\Sigma_{\mathrm{period}}
\]

\[
\sigma_{\mathrm{annual}} \approx \sqrt{k}\sigma_{\mathrm{period}}
\]

These rules assume conditions such as stable distributions and appropriate dependence treatment. They are not universally valid for every intraday, overlapping, or serially correlated return series.

Use product-specific calendar conventions. Do not automatically apply 252 periods to crypto data.

## 9.8 Exponentially weighted covariance

An exponentially weighted estimator gives greater weight to recent observations:

\[
\hat{\Sigma}_t =
(1-\lambda)
\sum_{j=0}^{\infty}
\lambda^j
(r_{t-j}-\mu_t)(r_{t-j}-\mu_t)^\top
\]

where \(0<\lambda<1\). In production, use a finite recursive implementation with documented initialization and missing-data behavior.

The approximate half-life in observations is:

\[
h = \frac{\ln(0.5)}{\ln(\lambda)}
\]

A shorter half-life adapts faster but can be noisier.

## 9.9 Covariance shrinkage

Ledoit–Wolf-style shrinkage combines the sample covariance with a structured target:

\[
\hat{\Sigma} = \delta F + (1-\delta)S
\]

where:

- \(S\) is the sample covariance.
- \(F\) is the chosen structured target.
- \(0\leq\delta\leq1\) is the estimated shrinkage intensity.

Possible targets include:

- Constant variance.
- Single-factor covariance.
- Constant correlation.
- Another justified structured matrix.

Do not select \(\delta\) by inspecting final holdout performance.

## 9.10 Covariance validity

A covariance matrix used by a quadratic optimizer should be symmetric and, where required, positive semidefinite.

Symmetry check:

\[
\|\Sigma-\Sigma^\top\| \leq \varepsilon_{\mathrm{sym}}
\]

Eigenvalue condition:

\[
\lambda_{\min}(\Sigma) \geq -\varepsilon_{\mathrm{PSD}}
\]

Tolerance values must reflect numerical precision and the selected solver.

If a matrix is invalid, investigate data alignment, missing values, numerical conditioning, and estimation choices. Do not silently modify it without recording the method and resulting model version.

## 9.11 Missing and asynchronous data

- Do not fill missing returns with zero by default.
- Define a policy for stale prices.
- Align observations using actual timestamps and calendars.
- Preserve the distinction between a market closure and a missing feed.
- Consider asynchronous covariance estimation where justified.
- Include data coverage in model diagnostics.

## 9.12 Exit criteria

- Covariance and factor models are versioned.
- Estimates use point-in-time data.
- Missing-data rules are explicit.
- Numerical validity is tested.
- Allocation sensitivity to covariance estimates is measured.

---

# 10. Step 5 — Portfolio risk measures

No single risk metric is sufficient. Phase 11 must use complementary measures with clearly defined assumptions.

## 10.1 Volatility

\[
\sigma_p = \sqrt{w^\top \Sigma w}
\]

Volatility treats upside and downside variation symmetrically. It does not directly measure tail losses.

## 10.2 Marginal risk contribution

For \(\sigma_p>0\):

\[
\operatorname{MRC}_i =
\frac{(\Sigma w)_i}{\sigma_p}
\]

## 10.3 Component risk contribution

\[
\operatorname{CRC}_i =
w_i\operatorname{MRC}_i
\]

Under the stated model:

\[
\sum_i \operatorname{CRC}_i = \sigma_p
\]

The equality is a useful implementation invariant. It applies to the chosen volatility definition and signed-weight convention; it does not replace nonlinear scenario analysis.

## 10.4 Risk contribution percentage

When total volatility is positive:

\[
RC_i^{\%} =
\frac{\operatorname{CRC}_i}{\sigma_p}
\]

These percentages can be negative for hedging positions. Do not force all risk contributions to be positive unless using a specifically defined alternative measure.

## 10.5 Value at Risk

For loss variable \(L\), a VaR at confidence level \(1-\alpha\) is:

\[
\operatorname{VaR}_{1-\alpha}(L)
=
\inf\{x:\Pr(L\leq x)\geq1-\alpha\}
\]

VaR estimates a loss quantile. It does not describe the average severity of losses beyond that quantile.

A normal approximation for portfolio returns may be used as a diagnostic:

\[
\operatorname{VaR}_{1-\alpha}
\approx
-\mu_p z_{\alpha}\,+\,z_{1-\alpha}\sigma_p
\]

For a zero-mean return model, a common simplified loss estimate is:

\[
\operatorname{VaR}_{1-\alpha}
\approx z_{1-\alpha}\sigma_p V
\]

where \(z_{1-\alpha}\) is the standard normal quantile and \(V\) is the defined portfolio value.

This is a model-based approximation, not a reliable universal tail-risk measure. Heavy tails, jumps, nonlinearity, and liquidity can invalidate it.

## 10.6 Expected shortfall / CVaR

For a continuous loss distribution:

\[
\operatorname{ES}_{1-\alpha}(L)
=
E[L\mid L\geq\operatorname{VaR}_{1-\alpha}(L)]
\]

For distributions with atoms or discrete outcomes, use a quantile-consistent expected-shortfall definition rather than relying blindly on the conditional-expectation expression.

## 10.7 Scenario CVaR optimization

Given scenario losses \(L_s(w)\), probabilities \(p_s\), threshold \(\eta\), and auxiliary variables \(u_s\), a common formulation is:

\[
u_s \geq L_s(w)-\eta
\]

\[
u_s\geq0
\]

\[
\operatorname{CVaR}_{1-\alpha}(w)
=
\eta+
\frac{1}{\alpha}
\sum_{s=1}^{S}p_s u_s
\]

with:

\[
\sum_{s=1}^{S}p_s=1
\]

For equal-weight scenarios, \(p_s=1/S\).

This is useful for optimization when scenario loss is linear or can be represented in a compatible convex formulation.

## 10.8 Drawdown

Let \(V_t\) be portfolio value under a specified net-of-cost accounting convention.

Running peak:

\[
H_t = \max_{0\leq s\leq t}V_s
\]

Drawdown:

\[
D_t = \frac{V_t}{H_t}-1
\]

Maximum drawdown over the interval:

\[
\operatorname{MDD} = \max_t(-D_t)
\]

Ensure the reported convention distinguishes drawdown as a negative return from maximum drawdown as a positive magnitude.

## 10.9 Expected loss and scenario loss

For scenario losses \(L_s\) and probabilities \(p_s\):

\[
E[L] = \sum_{s=1}^{S}p_s L_s
\]

\[
\sum_s p_s=1
\]

The probabilities must be justified. A stress scenario should not be presented as a probabilistically calibrated forecast unless it actually is one.

## 10.10 Concentration

For nonnegative normalized exposure shares \(a_i\) satisfying \(\sum_i a_i=1\), a concentration index is:

\[
HHI = \sum_i a_i^2
\]

This can be computed for issuer, venue, sector, or other groupings.

It measures concentration, not total portfolio risk. Use a defined nonnegative exposure basis, and report the basis alongside the metric.

## 10.11 Liquidity horizon

A simplified liquidation-time estimate is:

\[
T_i \approx \frac{|Q_i|}{q_i^{\mathrm{safe}}}
\]

where \(Q_i\) is the position size and \(q_i^{\mathrm{safe}}\) is an approved execution rate.

A volume-participation approximation is:

\[
q_i^{\mathrm{safe}} =
\pi_i \widehat{V}_i
\]

where \(\widehat{V}_i\) is estimated available volume over the relevant interval and \(\pi_i\) is a conservative participation fraction.

These are planning approximations, not guarantees of execution. Depth, spreads, volatility, venue limits, and market impact must also be considered.

## 10.12 Counterparty exposure

Track exposure by venue, custodian, broker, issuer, and platform. Define exposure according to the actual legal and operational relationship.

A single universal counterparty-loss formula is not sufficient for all products. Where counterparty default risk is modeled, specify probability of default, loss given default, and exposure at default under a documented methodology.

---

# 11. Step 6 — Global and section risk budgets

## 11.1 Hierarchical budget

```text
Global approved risk capacity
    |
    +-- Stocks
    +-- Options
    +-- Crypto
    +-- Forex
    +-- Bets/Predictions
    |
    +-- Shared-factor constraints
    +-- Liquidity constraints
    +-- Counterparty constraints
    +-- Collateral and margin constraints
    +-- Event and settlement constraints
```

A section budget is not independent capital. All section budgets must reconcile to the global policy.

## 11.2 Risk-budget constraints

Let \(RC_i(w)\) be the chosen risk contribution and \(b_i\) the approved target contribution.

A risk-budgeting objective can minimize deviation from target contributions:

\[
\min_w \sum_i \omega_i\left(RC_i(w)-b_i R_{\mathrm{target}}\right)^2
\]

subject to approved exposure and operational constraints.

This formulation may be non-convex because risk contributions depend on portfolio weights and risk normalization. It requires careful solver choice and validation.

An alternative positive-weight risk-budgeting objective under a covariance model is:

\[
\min_{x>0}
\quad
\frac{1}{2}x^\top\Sigma x-\sum_i b_i\ln x_i
\]

where \(b_i>0\). This objective has a convex structure when \(\Sigma\) is positive semidefinite and the feasible domain is positive. Its solution is a risk-budgeting construction for the modeled assets; it does not by itself solve all of AXEL's cross-asset constraints.

## 11.3 Global constraint examples

Budget and limits must come from approved policy, not model output.

Examples:

\[
G(w)\leq G_{\max}
\]

\[
E_k(w)\leq E_{k,\max}
\]

\[
\operatorname{CVaR}_{1-\alpha}(w)\leq C_{\max}
\]

\[
\operatorname{Margin}(w)\leq M_{\max}
\]

\[
\operatorname{LiquidityRisk}(w)\leq L_{\max}
\]

The exact functions must be defined per product and account.

## 11.4 Outstanding commitments

Available capacity must account for positions and possible fills from open orders.

Do not add current position exposure and order exposure blindly if an order reduces an existing position or if orders are mutually exclusive. Model the relevant order/fill states or use a conservative approved reservation method.

## 11.5 Missing information

If an exposure cannot be calculated, it must not be treated as zero. Depending on severity, the system should block risk-increasing proposals, reserve a conservative bound, or request reconciliation.

## 11.6 Exit criteria

- Budgets reconcile.
- Global limits cannot be exceeded by section allocations.
- Outstanding commitments are included.
- Unknown exposures do not increase risk capacity.
- Hard limits cannot be relaxed by the optimizer.

---

# 12. Step 7 — Portfolio optimization

## 12.1 Principle

Start with simple, transparent methods. Add sophistication only when it demonstrates robust improvement over fair baselines.

## 12.2 Baselines

Implement suitable versions of:

1. No rebalance.
2. Equal weight where meaningful.
3. Inverse volatility.
4. Minimum variance.
5. Risk budgeting.
6. Constrained mean-variance optimization.
7. Robust optimization.
8. CVaR-based allocation where appropriate.

Do not apply equal weighting or inverse volatility indiscriminately to derivatives, event contracts, or assets with incomparable capital and payoff definitions.

## 12.3 Inverse-volatility baseline

For positive estimated volatilities \(\sigma_i\):

\[
\widetilde{w}_i=\frac{1}{\sigma_i}
\]

Normalize:

\[
w_i=\frac{\widetilde{w}_i}{\sum_j\widetilde{w}_j}
\]

This ignores correlations. It is a baseline, not a full risk-parity solution.

## 12.4 Minimum-variance allocation

A basic fully invested long-only problem is:

\[
\min_w \quad w^\top\Sigma w
\]

subject to:

\[
\mathbf{1}^\top w=1
\]

\[
w_i\geq0
\]

This minimizes modeled variance, not expected loss or tail risk. Real-world constraints and costs may materially change the solution.

## 12.5 Mean-variance objective

A common objective is:

\[
\max_w
\quad
\mu^\top w
-\frac{\lambda}{2}w^\top\Sigma w
\]

subject to approved constraints.

Here \(\lambda\) controls the trade-off between expected return and modeled variance. Its value and interpretation depend on the units and scaling of \(\mu\), \(\Sigma\), and the objective.

Never calibrate \(\lambda\) by maximizing performance on the final holdout.

## 12.6 Transaction-cost-aware objective

Let \(w_0\) be the current allocation:

\[
\max_w
\quad
\mu^\top w
-\frac{\lambda}{2}w^\top\Sigma w
-C(w,w_0)
\]

A simple turnover penalty is:

\[
C_{\mathrm{turnover}}(w,w_0)
=
\sum_i c_i |w_i-w_{0,i}|
\]

where \(c_i\) is a cost coefficient consistent with the chosen weight and return units.

The absolute-value term can be represented with auxiliary variables in a linear or convex optimization formulation.

## 12.7 Robust expected return

If expected return is uncertain within a set \(\mathcal U_\mu\), a robust objective is:

\[
\max_w
\quad
\min_{\mu\in\mathcal U_\mu}\mu^\top w
-\frac{\lambda}{2}w^\top\Sigma w
-C(w,w_0)
\]

The uncertainty set must be justified and calibrated. An overly narrow set creates false confidence; an overly wide set may produce unusable allocations.

## 12.8 Robust covariance

If covariance belongs to an uncertainty set \(\mathcal U_\Sigma\), a conservative formulation is:

\[
\min_w\max_{\Sigma\in\mathcal U_\Sigma} w^\top\Sigma w
\]

This can be computationally expensive and may not remain a simple convex program for every uncertainty-set design. Document the selected method and solver.

## 12.9 Generic quadratic program

A standard quadratic program is:

\[
\min_x\quad \frac12 x^\top P x+q^\top x
\]

subject to:

\[
Gx\leq h
\]

\[
Ax=b
\]

where \(P\) is positive semidefinite for a convex quadratic objective.

Official reference: https://cvxpy.readthedocs.io/en/functions-table/examples/basic/quadratic_program.html

## 12.10 Allocation constraints

Potential constraints include:

### Fully invested

\[
\mathbf{1}^\top w=1
\]

Only use this when the weights actually represent a fully invested portfolio. It is not automatically appropriate for multi-currency, derivatives, or multi-account portfolios.

### Long-only

\[
w_i\geq0
\]

### Per-instrument bounds

\[
l_i\leq w_i\leq u_i
\]

### Group limits

For grouping matrix \(A_g\):

\[
l_g\leq A_gw\leq u_g
\]

### Turnover bound

\[
\sum_i |w_i-w_{0,i}|\leq T_{\max}
\]

### Factor exposure bounds

\[
f_{\min}\leq B^\top w\leq f_{\max}
\]

### Risk bound

\[
w^\top\Sigma w\leq \sigma_{\max}^2
\]

### Budget or collateral bound

\[
M(w)\leq M_{\max}
\]

Each constraint must use the correct exposure definition and units. A market-value constraint is not automatically a maximum-loss or margin constraint.

## 12.11 Solver failure

The optimizer must distinguish:

- Optimal solution.
- Acceptable approximate solution.
- Infeasible problem.
- Numerical failure.
- Timeout.
- Invalid input.
- Unsupported instrument.
- Missing mandatory risk input.

A failed solver must never cause the system to remove constraints and retry unconstrained.

If no valid solution exists, return a structured failure or an explicitly approved safe fallback.

## 12.12 Stability analysis

Perturb inputs within documented plausible ranges:

\[
\mu'=\mu+\Delta\mu
\]

\[
\Sigma'=\Sigma+\Delta\Sigma
\]

Then compare candidate weights:

\[
S_w=\|w'-w\|
\]

The norm and scaling must be specified. A high \(S_w\) indicates sensitivity, not automatically a bad allocation. Establish thresholds through research and testing.

## 12.13 Exit criteria

- Every allocation method has a documented objective.
- All constraints are checked independently.
- Solver status is persisted.
- Baselines are evaluated.
- Sensitivity and uncertainty tests are included.
- No solver failure can authorize a trade.

---

# 13. Step 8 — Transaction costs and liquidity

## 13.1 Total cost model

For a proposed trade, estimate total cost as:

\[
C_{\mathrm{total}}
=
C_{\mathrm{fees}}
+C_{\mathrm{spread}}
+C_{\mathrm{slippage}}
+C_{\mathrm{impact}}
+C_{\mathrm{funding}}
+C_{\mathrm{borrow}}
+C_{\mathrm{conversion}}
+C_{\mathrm{settlement}}
\]

Include only applicable terms and avoid double-counting fees or spreads embedded in execution prices.

## 13.2 Bid-ask spread

For best ask \(A\) and best bid \(B\), the absolute spread is:

\[
S=A-B
\]

Mid-price:

\[
M=\frac{A+B}{2}
\]

Relative spread:

\[
S_{\mathrm{rel}}=\frac{A-B}{M}
\]

A rough half-spread cost for a small marketable order is:

\[
C_{\mathrm{spread}}\approx \frac{A-B}{2}|Q|
\]

where \(Q\) is the traded quantity in matching units.

Actual cost depends on side, depth, fill price, and order type.

## 13.3 Linear fees

For fee rate \(f_i\) and traded notional \(N_i\):

\[
C_{\mathrm{fees},i}=f_i|N_i|
\]

Include minimum fees, tiered fees, rebates, and venue-specific rules where material.

## 13.4 Slippage

For actual fill price \(P_{\mathrm{fill}}\) and a declared reference price \(P_{\mathrm{ref}}\):

\[
C_{\mathrm{slippage}}
=
s\cdot Q(P_{\mathrm{fill}}-P_{\mathrm{ref}})
\]

where \(s=+1\) for a buy and \(s=-1\) for a sell under the convention that positive cost is adverse. The implementation must handle signed quantities consistently.

## 13.5 Market impact

A simplified empirical model might take the form:

\[
I_i(Q)=a_i\sigma_i\left(\frac{|Q|}{V_i}\right)^\beta
\]

where:

- \(\sigma_i\) is a volatility measure.
- \(V_i\) is an appropriate volume or liquidity scale.
- \(a_i\) and \(\beta\) are calibrated parameters.

This is only a candidate model form, not a universal law. Estimate parameters from execution data where possible and validate out of sample. Do not extrapolate beyond the data's supported range without conservative controls.

## 13.6 Funding and borrow

For notional \(N\), annualized financing rate \(r_f\), and holding time \(\Delta t\) expressed as a fraction of the year:

\[
C_{\mathrm{funding}}\approx |N|r_f\Delta t
\]

This approximation assumes a simple rate convention. Actual crypto funding, Forex rollover, stock borrow, and margin financing require venue-specific schedules and compounding rules.

## 13.7 Net expected return

For a proposed allocation with expected gross return \(E[R_{\mathrm{gross}}]\):

\[
E[R_{\mathrm{net}}]
=
E[R_{\mathrm{gross}}]
-E[C_{\mathrm{total}}]
\]

This equation requires both terms to be expressed on the same portfolio base and horizon.

## 13.8 Turnover

One common one-way turnover definition is:

\[
TO=\sum_i |w_i-w_{0,i}|
\]

Some institutions use half this amount to represent buys or sells when the portfolio is self-financing. AXEL must define and consistently use one convention.

## 13.9 Cost stress

Evaluate costs under multiple assumptions:

\[
C_{\mathrm{stress}}=kC_{\mathrm{base}}
\]

for explicitly selected multipliers \(k\), supplemented by spread, liquidity, and impact scenarios. Do not interpret one multiplier as a calibrated probability distribution.

## 13.10 Exit criteria

Backtests and shadow runs must report:

- Gross P&L.
- Fees.
- Spread cost.
- Slippage.
- Market impact.
- Financing and borrow.
- Net P&L.
- Turnover.
- Cost forecast error.
- Cost stress results.

---

# 14. Step 9 — Options and nonlinear exposure

Options require product-specific risk calculations. Linear covariance models alone are insufficient.

## 14.1 Black–Scholes reference model

For a European call with no dividends under the standard Black–Scholes assumptions:

\[
C=S_0N(d_1)-Ke^{-rT}N(d_2)
\]

For a European put:

\[
P=Ke^{-rT}N(-d_2)-S_0N(-d_1)
\]

where:

\[
d_1=
\frac{\ln(S_0/K)+(r+\frac12\sigma^2)T}
{\sigma\sqrt{T}}
\]

\[
d_2=d_1-\sigma\sqrt{T}
\]

Definitions:

- \(S_0\): current underlying price.
- \(K\): strike.
- \(r\): continuously compounded risk-free rate under the model.
- \(T\): time to expiry in years.
- \(\sigma\): volatility.
- \(N(\cdot)\): standard normal cumulative distribution function.

For dividend-paying assets, use an appropriate dividend yield or forward-price formulation. American-style options may require a different pricing model. The model assumptions must be documented.

## 14.2 Greeks

For a non-dividend-paying European call in the Black–Scholes model:

\[
\Delta_C=N(d_1)
\]

For a put:

\[
\Delta_P=N(d_1)-1
\]

Gamma:

\[
\Gamma=
\frac{\phi(d_1)}{S_0\sigma\sqrt{T}}
\]

where \(\phi(\cdot)\) is the standard normal probability density function.

Vega:

\[
\nu=S_0\phi(d_1)\sqrt{T}
\]

This is the derivative of option value with respect to volatility expressed as a decimal. If the system reports vega per one percentage-point change in implied volatility, the reported value is typically scaled by \(0.01\).

Theta and rho depend on option type, dividends, rates, and the exact model convention. Implement and independently test the correct derivatives for the chosen model rather than copying a single formula across all products.

## 14.3 Local option P&L approximation

For small changes:

\[
\Delta V
\approx
\Delta\,\Delta S
+\frac12\Gamma(\Delta S)^2
+\nu\,\Delta\sigma
+\Theta\,\Delta t
+\rho\,\Delta r
\]

For a portfolio of contracts, multiply per-contract sensitivities by signed contract count and contract multiplier.

This approximation is local. Large price jumps, volatility-surface changes, near-expiry behavior, and discontinuities require full repricing or scenario-based treatment.

## 14.4 Option portfolio aggregation

For option positions \(j\):

\[
\Delta_{\mathrm{portfolio}}
=
\sum_j q_j M_j\Delta_j
\]

\[
\Gamma_{\mathrm{portfolio}}
=
\sum_j q_j M_j\Gamma_j
\]

\[
\nu_{\mathrm{portfolio}}
=
\sum_j q_j M_j\nu_j
\]

These aggregate sensitivities must be reported in consistent units. Greeks do not replace maximum-loss, margin, or full-revaluation stress tests.

## 14.5 Required option scenarios

At minimum evaluate relevant combinations of:

- Underlying price shocks.
- Implied-volatility shifts.
- Volatility skew changes.
- Time decay.
- Interest-rate changes.
- Gap moves.
- Expiry and settlement effects.
- Margin changes.
- Exercise or assignment.

Use actual instrument specifications and venue rules.

## 14.6 Exit criteria

- Greeks match verified test examples.
- Contract multipliers and signs are correct.
- Expiry and settlement are handled.
- Large-shock scenarios use appropriate repricing.
- Option risk cannot be reduced to premium value alone.

---

# 15. Step 10 — FX, crypto, and prediction-market exposure

## 15.1 FX currency aggregation

Let \(e_c\) be the signed exposure to currency \(c\), expressed in the reporting currency. The portfolio's currency vector is:

\[
e=(e_1,e_2,\ldots,e_k)^\top
\]

If \(x\) is a vector of currency returns against a reporting currency, a first-order P&L approximation is:

\[
\Delta V_{\mathrm{FX}}\approx e^\top x
\]

Cross rates, nonlinear contracts, and broker-specific settlement require a consistent valuation convention.

## 15.2 FX cross-rate consistency

For currency conversion rates quoted consistently:

\[
X_{A\rightarrow C}
=
X_{A\rightarrow B}X_{B\rightarrow C}
\]

Discrepancies may indicate inconsistent timestamps, quote conventions, or data errors. Do not treat this identity as a guaranteed executable arbitrage opportunity after spreads and costs.

## 15.3 Crypto funding

For notional \(N\), funding rate \(f_k\) at settlement interval \(k\), and the convention that positive funding is paid by a long position:

\[
C_{\mathrm{funding}}=\sum_k sN_k f_k
\]

where \(s\) depends on position direction and the venue's payment convention. Confirm the exact sign convention from the venue. Funding can be received or paid; never assume it is always a cost.

## 15.4 Prediction-market payoffs

Let \(Y\) denote an event outcome and \(g(Y)\) the contract's net payoff at settlement.

Expected payoff:

\[
E[g(Y)]=\sum_y P(Y=y)g(y)
\]

Expected value net of entry cost \(c\):

\[
EV=E[g(Y)]-c
\]

For a binary contract paying one unit if an event occurs and zero otherwise, purchased at price \(p\), with no additional fees:

\[
EV=P(Y=1)-p
\]

This is per contract. Real contracts may include fees, variable payouts, settlement rules, or liquidity costs.

For a position of \(q\) contracts:

\[
EV_q=q\left(P(Y=1)-p\right)-C_{\mathrm{additional}}
\]

Use actual contract payoff schedules rather than assuming every platform follows this binary example.

## 15.5 Event dependency

If events \(A\) and \(B\) are dependent:

\[
P(A\cap B)=P(A)P(B\mid A)
\]

Do not assume:

\[
P(A\cap B)=P(A)P(B)
\]

unless independence is justified.

Prediction contracts tied to the same election, match, economic release, or underlying event must be grouped by their common dependency. Marginal event probabilities alone may not adequately describe joint portfolio loss.

## 15.6 Maximum loss

For each position, calculate the maximum loss where the contract payoff permits it.

For a defined-loss position with initial cost \(c\) and nonnegative settlement payout \(g(Y)\):

\[
L_{\max}=\max_Y(c-g(Y))
\]

subject to the actual settlement and fee rules.

For positions with theoretically unbounded losses, do not invent a finite maximum-loss value. Use a validated exposure, margin, stress, or loss-limit framework.

## 15.7 Exit criteria

- Currency legs aggregate correctly.
- Venue-specific funding and financing are included.
- Event dependencies are represented.
- Payoff and settlement logic is tested.
- Unsupported payout structures are blocked or handled through an explicitly approved model.

---

# 16. Step 11 — Stress testing and robust optimization

## 16.1 Scenario families

Create versioned scenarios for:

- Broad equity sell-off.
- Sector shocks.
- Interest-rate level and curve changes.
- Currency shocks.
- Crypto crashes.
- Volatility spikes.
- Liquidity collapse.
- Wider spreads and higher impact.
- Funding and margin increases.
- Exchange or custodian outages.
- Counterparty failure.
- Prediction-market event resolution.
- Settlement delay.
- Correlation increases.
- Simultaneous failures.

## 16.2 Scenario P&L

For scenario \(s\):

\[
\Delta V_s=V_s^{\mathrm{stressed}}-V^{\mathrm{base}}
\]

Scenario loss:

\[
L_s=-\Delta V_s
\]

For a portfolio of positions with linear scenario sensitivities, a first-order approximation may be used:

\[
\Delta V_s\approx J\Delta z_s
\]

where \(J\) is the sensitivity matrix and \(\Delta z_s\) is the vector of shocked risk factors.

For nonlinear positions, use full revaluation where material.

## 16.3 Combined stress

Do not test only one factor at a time. Include plausible joint scenarios such as:

- Market sell-off plus volatility spike.
- Market sell-off plus liquidity deterioration.
- Crypto decline plus venue outage.
- Currency shock plus financing increase.
- Event settlement shock plus platform withdrawal restriction.

Scenario probabilities must not be invented. If the scenario is hypothetical, label it as hypothetical.

## 16.4 Robustness tests

Perturb:

- Expected returns.
- Covariance estimates.
- Costs.
- Liquidity.
- Correlations.
- Model parameters.
- Instrument universe.
- Start and end dates.

Record how portfolio weights, risk, turnover, and net performance change.

## 16.5 Exit criteria

A proposal must pass the approved stress suite or be rejected/deferred according to policy. A proposal cannot bypass stress constraints because its expected return is high.

---

# 17. Step 12 — Allocation proposals and deterministic authorization

## 17.1 Proposal contract

A proposal should include:

- Proposal ID.
- Portfolio snapshot ID.
- Creation time.
- Input cut-off time.
- Expiry time.
- Instrument and section IDs.
- Current and proposed exposure.
- Proposed delta.
- Estimated costs.
- Risk contribution before and after.
- Constraint evaluation.
- Stress-test references.
- Model and configuration versions.
- Solver status.
- Source signal references.
- Explanation.
- Known limitations.
- Data-quality status.
- Proposed action: increase, reduce, hold, or defer.

## 17.2 Proposal freshness

A proposal is usable only if its inputs remain valid under the approved freshness policy.

Define age:

\[
\operatorname{Age}=t_{\mathrm{consume}}-t_{\mathrm{cutoff}}
\]

If:

\[
\operatorname{Age}>\tau_{\mathrm{max}}
\]

the proposal must be rejected or revalidated according to policy. \(\tau_{\mathrm{max}}\) must be instrument- and strategy-appropriate, not one universal constant.

## 17.3 Required core checks

The deterministic core independently verifies:

1. Schema validity.
2. Source and proposal provenance.
3. Freshness.
4. Current positions and open orders.
5. Capital and collateral.
6. Global and section limits.
7. Instrument eligibility.
8. Existing kill-switch state.
9. Execution-specific constraints.
10. Required human approvals.
11. Idempotency and duplicate submission protection.
12. Any additional established safety checks.

## 17.4 Authorization invariant

A proposal that cannot be fully validated must not be authorized.

A portfolio optimizer's success status is not authorization.

## 17.5 Explanation contract

The explanation must distinguish:

- Observed facts.
- Statistical estimates.
- Optimization results.
- Scenario assumptions.
- Policy limits.
- Missing information.
- Known model limitations.

It must not claim that a proposal is safe or profitable merely because it passed a model.

---

# 18. Step 13 — Persistence, events, and audit

## 18.1 Suggested logical records

Map these to existing schemas before adding migrations.

- `PortfolioSnapshot`
- `PositionSnapshot`
- `CommitmentSnapshot`
- `ExposureSnapshot`
- `RiskBudgetSnapshot`
- `RiskMetric`
- `StressTestRun`
- `AllocationRun`
- `AllocationProposal`
- `RebalanceProposal`
- `ReconciliationIssue`
- `PortfolioModelVersion`
- `AllocationAuditEvent`

## 18.2 Snapshot fields

A snapshot should include:

- Snapshot ID.
- Account or portfolio ID.
- Snapshot timestamp.
- Source cut-off time.
- Data freshness.
- Completeness status.
- Source references.
- Valuation currency.
- Position count.
- Open-order count.
- Reconciliation state.
- Schema version.
- Calculation version.

## 18.3 Reproducibility

A quantitative run should be reproducible from:

- Code version.
- Configuration version.
- Data snapshot.
- Universe definition.
- Timestamp and calendar policy.
- Model version.
- Solver version.
- Random seed where applicable.
- Input validation results.

For nondeterministic numerical algorithms, document expected numerical tolerance rather than claiming exact bitwise reproducibility.

## 18.4 Event processing

Use idempotent handlers, explicit event IDs, and transactional persistence where required.

A replayed event must not create duplicate positions, fills, cash movements, or proposals.

## 18.5 Storage architecture

Prefer the existing PostgreSQL/TimescaleDB system of record for authoritative snapshots and audit metadata if it satisfies requirements.

Use analytical stores or object storage only where they solve a demonstrated need, such as large historical datasets or analytical workloads. Do not duplicate authoritative state across databases without a clear consistency model.

## 18.6 Audit questions

For every proposal, the system should answer:

1. What did AXEL know at the time?
2. Which data was missing or stale?
3. Which positions and commitments were included?
4. Which model and configuration were used?
5. Which constraints were evaluated?
6. Which scenarios were run?
7. What did the optimizer propose?
8. What did the deterministic core decide?
9. What was executed?
10. How did actual costs and risk compare with estimates?

---

# 19. Step 14 — Quantitative validation and statistical testing

## 19.1 Point-in-time integrity

Every input used to make a decision at time \(t\) must have been available by \(t\).

Formally, for every feature \(x_t\):

\[
t_{\mathrm{available}}(x_t)\leq t_{\mathrm{decision}}
\]

If a value was published later, revised later, or only became known after settlement, it cannot be used as though it were known at decision time.

## 19.2 Walk-forward evaluation

A basic walk-forward process:

1. Select a training interval.
2. Fit or estimate models using that interval only.
3. Freeze the model and configuration.
4. Evaluate on the subsequent interval.
5. Advance the window.
6. Repeat across multiple regimes.
7. Aggregate only genuinely out-of-sample results.
8. Preserve a final untouched holdout.

Avoid random shuffling that allows future observations to inform past decisions.

## 19.3 Net performance

For period \(t\):

\[
R_{t,\mathrm{net}}=R_{t,\mathrm{gross}}-C_t
\]

This is valid when costs are expressed in the same units and on the same return base. If costs are cash amounts, convert them consistently before subtraction.

## 19.4 Sharpe ratio

For periodic excess returns \(x_t=r_t-r_{f,t}\):

\[
\widehat{SR}=\frac{\bar{x}}{s_x}
\]

A common annualized approximation is:

\[
\widehat{SR}_{\mathrm{annual}}=\sqrt{k}\widehat{SR}
\]

where \(k\) is the number of consistent periods per year. Serial correlation and non-normality can invalidate naïve inference from this annualized statistic.

## 19.5 Sortino ratio

For a target return \(T\) and defined downside deviation \(DD\):

\[
\operatorname{Sortino}=
\frac{\bar r-T}{DD}
\]

The downside-deviation convention must be specified. There are multiple implementations, so all reports must use the same definition.

## 19.6 Calmar ratio

For annualized return \(R_{\mathrm{ann}}\) and positive maximum-drawdown magnitude:

\[
\operatorname{Calmar}=
\frac{R_{\mathrm{ann}}}{\operatorname{MDD}}
\]

This is undefined when MDD is zero and must be handled explicitly.

## 19.7 Tracking error

For active returns \(a_t=r_{p,t}-r_{b,t}\):

\[
TE_{\mathrm{period}}=s(a_t)
\]

\[
TE_{\mathrm{annual}}\approx\sqrt{k}\,s(a_t)
\]

where \(r_p\) is portfolio return and \(r_b\) is benchmark return.

## 19.8 Turnover and costs

Report turnover and all major cost components alongside performance. An apparent improvement that depends on unrealistic execution costs must fail validation.

## 19.9 Multiple testing

Track every candidate method and materially different parameter configuration. Do not report only the winner without recording the search process.

Consider:

- Deflated Sharpe Ratio.
- Probability of Backtest Overfitting.
- Multiple-testing adjustments appropriate to the hypothesis family.
- Final untouched holdout.
- Robustness across time periods.
- Independent paper validation.

The precise statistical procedure depends on the research design. These methods are not interchangeable universal corrections.

## 19.10 Confidence intervals

Where suitable, estimate uncertainty using a method that respects the time-series structure, such as a justified block bootstrap.

Do not use IID bootstrap assumptions without checking serial dependence, volatility clustering, and overlapping returns.

## 19.11 Baseline comparison

Compare all candidate allocation methods against the same:

- Data universe.
- Time window.
- Costs.
- Trading calendar.
- Constraints.
- Execution assumptions.
- Evaluation metrics.

A more complex method should demonstrate a repeatable improvement over a simpler method before it becomes the default.

## 19.12 Exit criteria

- Leakage checks pass.
- Costs and constraints are realistic.
- Baselines are included.
- Experiments are tracked.
- Holdout data remains untouched.
- Results are reproducible.
- Statistical limitations are documented.

---

# 20. Step 15 — Shadow mode and release gates

## 20.1 Shadow mode

Phase 11 consumes real validated state and generates proposals but does not submit orders.

Measure:

- Snapshot completeness.
- Data freshness.
- Reconciliation discrepancy rate.
- Solver failures.
- Constraint rejection rate.
- Proposal turnover.
- Predicted versus realized costs.
- Risk estimate stability.
- Stress-test outcomes.
- Proposal age.
- Performance relative to baselines.

## 20.2 Required evidence

Before enabling downstream use, collect evidence that:

- Snapshots reconcile.
- No stale proposal is consumed.
- Global limits remain intact.
- Unsupported instruments are handled safely.
- Optimizer failures do not remove constraints.
- The deterministic core rejects invalid proposals.
- The Watchdog remains independent.
- Monitoring and alerts work.
- Recovery and rollback procedures are tested.

## 20.3 Release criteria

A release requires:

- Passing CI.
- Reviewed migrations.
- Security checks.
- Quantitative validation.
- Shadow-mode evidence.
- Documented alert ownership.
- Rollback procedures.
- Human approval where required by AXEL policy.

The required shadow period and numeric thresholds must be specified before evaluation and must not be chosen after inspecting results.

---

# 21. Software engineering standards

These are project requirements, not claims that one research paper mandates every item.

## 21.1 Contracts first

For every module, document:

- Purpose.
- Non-goals.
- Inputs and outputs.
- Units.
- Time semantics.
- Preconditions and postconditions.
- Error behavior.
- Data ownership.
- Permissions.
- Invariants.
- Tests.
- Acceptance criteria.

## 21.2 Code organization

Prefer small modules with explicit interfaces:

- Domain types.
- Pure quantitative functions.
- Data-access adapters.
- External API adapters.
- Service orchestration.
- Policy and validation.
- Persistence.
- Monitoring.

Keep quantitative calculations separate from database and network side effects where practical.

## 21.3 Determinism

Given identical validated inputs and configuration, a deterministic calculation should return the same logical output within defined numerical tolerance.

Do not claim exact floating-point equality when solver implementation or hardware can cause small numerical differences.

## 21.4 Type and schema validation

Validate:

- IDs.
- Units.
- Currency.
- Timestamps.
- Finite numeric values.
- Bounds.
- Array dimensions.
- Matrix dimensions.
- Instrument eligibility.
- Solver status.

Reject NaN, infinity, malformed matrices, and unknown enum values.

## 21.5 Database migrations

- Use the existing migration framework.
- Add constraints where appropriate.
- Keep migrations reviewable.
- Test migration and rollback behavior.
- Preserve historical audit data.
- Avoid destructive schema changes without an approved migration plan.

## 21.6 CI

Required checks should include:

- Formatting and linting.
- Type checking where supported.
- Unit tests.
- Property-based tests.
- Integration tests.
- Migration tests.
- Dependency security scanning.
- Deterministic-core boundary checks.
- Coverage for critical invariants.
- Build reproducibility.

Coverage percentage alone does not establish correctness.

## 21.7 Code review

Risk-affecting changes require review. The agent implementing a change must not be the sole authority approving its own risk policy, authorization, or production promotion.

## 21.8 Documentation

Every material algorithm must document:

- Formula.
- Assumptions.
- Units.
- Data requirements.
- Known failure modes.
- Calibration procedure.
- Validation results.
- Version history.

---

# 22. Module breakdown and interfaces

These are logical modules. Reuse existing AXEL components if they already meet the requirements.

| Module | Responsibility |
|---|---|
| `P11-AUDIT` | Repository audit and architecture decisions |
| `P11-STATE` | Portfolio snapshots |
| `P11-RECON` | Reconciliation |
| `P11-REGISTRY` | Instrument and factor registry |
| `P11-NORMALIZE` | Exposure normalization |
| `P11-RISK` | Risk estimates and contributions |
| `P11-BUDGET` | Global and section budgets |
| `P11-COST` | Cost and liquidity models |
| `P11-OPTIMIZER` | Candidate allocation methods |
| `P11-STRESS` | Scenario generation and valuation |
| `P11-PROPOSAL` | Proposals and explanations |
| `P11-PERSIST` | Storage and lineage |
| `P11-OBSERVABILITY` | Logs, metrics, alerts |
| `P11-GOVERNANCE` | Authorization integration and release gates |
| `P11-QA` | Tests and quantitative validation |

## 22.1 Interface principle

The optimizer should accept validated data structures and return a typed result. It should not reach directly into broker APIs, modify risk settings, or execute orders.

A conceptual interface is:

```python
def generate_allocation(
    portfolio_snapshot,
    exposure_snapshot,
    risk_budget,
    model_config,
) -> AllocationResult:
    ...
```

This is illustrative. The developer must adapt it to the existing repository's types, conventions, and APIs.

## 22.2 Result status

The result should represent success and failure explicitly, for example:

- `OPTIMAL`
- `APPROXIMATE`
- `INFEASIBLE`
- `INVALID_INPUT`
- `STALE_DATA`
- `MISSING_EXPOSURE`
- `SOLVER_ERROR`
- `TIMEOUT`
- `UNSUPPORTED_INSTRUMENT`

Do not encode failure as an empty allocation that might be misinterpreted as a valid zero-risk target.

---

# 23. Security, reliability, and operational controls

## 23.1 Reliability

Implement:

- Explicit timeouts.
- Bounded retries.
- Idempotent processing.
- Quarantine for malformed messages.
- Health and readiness checks.
- Staleness detection.
- Backpressure.
- Recovery for interrupted runs.
- Database transaction boundaries.
- Alerting on persistent discrepancies.

## 23.2 Security

- Least-privilege service accounts.
- Separate read and write database roles.
- No secrets in logs.
- Authenticated internal APIs.
- Strict input validation.
- Dependency pinning and vulnerability checks.
- Audit records for privileged actions.
- No arbitrary LLM-generated code execution in trusted services.
- No LLM-generated SQL with privileged credentials.
- No new execution path that bypasses the core.

## 23.3 Fail-safe policy

If required state cannot be established, the default is no new risk-increasing proposal.

Whether risk-reducing proposals remain permissible during degraded operation must be specified by policy and enforced by the deterministic core. Phase 11 must not invent its own exception.

## 23.4 Monitoring

Monitor at least:

- Data freshness.
- Snapshot completeness.
- Reconciliation discrepancies.
- Exposure coverage.
- Covariance validity.
- Solver status.
- Constraint violations.
- Cost forecast error.
- Stress results.
- Proposal age.
- Rejection reasons.
- Execution differences.
- Service availability.

---

# 24. Acceptance criteria

## 24.1 Data

- [ ] Portfolio snapshots contain source timestamps.
- [ ] Open orders and commitments are represented.
- [ ] Duplicate events are handled safely.
- [ ] Reconciliation issues are surfaced.
- [ ] Missing exposure cannot silently disappear.
- [ ] Currency and contract conventions are explicit.

## 24.2 Mathematics

- [ ] Return and covariance calculations are tested.
- [ ] Covariance validity is checked.
- [ ] Risk contributions reconcile.
- [ ] Nonlinear instruments receive appropriate risk treatment.
- [ ] Cost calculations use consistent units.
- [ ] Risk budgets reconcile to the global limit.
- [ ] Stress calculations are reproducible.
- [ ] Solver constraints are independently verified.

## 24.3 Quantitative validation

- [ ] Point-in-time integrity checks pass.
- [ ] Walk-forward validation exists.
- [ ] Baselines are included.
- [ ] Transaction costs are included.
- [ ] Multiple-testing risk is documented.
- [ ] Final holdout remains untouched.
- [ ] Robustness and stress tests pass.
- [ ] Results can be reproduced.

## 24.4 Safety

- [ ] Phase 11 cannot submit orders directly.
- [ ] The deterministic core independently validates proposals.
- [ ] Existing risk limits remain authoritative.
- [ ] The independent Watchdog remains independent.
- [ ] Solver failures cannot relax constraints.
- [ ] Missing data cannot increase available risk.
- [ ] Audit evidence exists for every proposal.

## 24.5 Operations

- [ ] CI and integration tests pass.
- [ ] Shadow mode meets pre-approved criteria.
- [ ] Monitoring and alert ownership are documented.
- [ ] Recovery and rollback are tested.
- [ ] Release approval is recorded.

Any failed mandatory safety criterion blocks release.

---

# 25. Implementation sequence for OpenCode

Implement in this order:

1. Audit the repository and current interfaces.
2. Produce an implementation plan with exact files and dependencies.
3. Resolve unresolved accounting and risk-policy assumptions.
4. Implement portfolio snapshots and reconciliation.
5. Implement instrument and risk-factor registry.
6. Implement exposure normalization.
7. Implement covariance and risk estimates.
8. Implement risk contributions and global budgets.
9. Implement baseline allocation methods.
10. Add transaction costs and liquidity.
11. Add options and nonlinear risk treatment.
12. Add stress testing.
13. Add robust allocation methods only after baseline correctness.
14. Implement proposal contracts and explanations.
15. Persist run metadata and audit lineage.
16. Integrate with the existing deterministic core.
17. Add property-based, integration, security, and quantitative tests.
18. Run shadow mode.
19. Produce evidence and request release approval.

## 25.1 Developer must not

- Modify risk limits to make the optimizer feasible.
- Bypass the deterministic core.
- Write orders or fills directly.
- Treat missing data as zero.
- Drop unsupported exposures silently.
- Use future data in historical validation.
- Select a model only by its best backtest.
- Merge risk-affecting changes without the required review.
- Treat this specification as proof that the repository already satisfies it.

---

# 26. Research library, papers, PDFs, and technical documentation

The following links are provided so the developer can study the underlying methods. Access rights and availability of full-text PDFs vary by publisher. Prefer author-hosted PDFs or institutional repositories when legally available.

## 26.1 Portfolio optimization and covariance

### A. Ledoit and Wolf — Honey, I Shrunk the Sample Covariance Matrix

- **Direct PDF:** https://www.ledoit.net/honey.pdf
- **Publisher DOI:** https://doi.org/10.3905/jpm.2004.110
- **Academic record:** https://bse.eu/research/working-papers/honey-i-shrunk-sample-covariance-matrix
- **Implementation documentation:** https://pyportfolioopt.readthedocs.io/en/latest/RiskModels.html

Read for covariance shrinkage, estimation error, and portfolio stability.

### B. Robust portfolio optimization

- **Publisher paper:** https://doi.org/10.1080/14697688.2020.1849780

Read for uncertainty-aware portfolio construction and robust optimization.

### C. Bounded-noise portfolio construction

- **Publisher paper:** https://doi.org/10.1287/opre.2019.1858

Read for the dangers of error amplification during optimization.

### D. Multi-portfolio risk budgeting

- **ResearchGate:** https://www.researchgate.net/publication/283557924_Risk-budgeting_multi-portfolio_optimization_with_portfolio_and_marginal_risk_constraints

Read for portfolio-wide risk constraints and risk budgeting.

### E. Transaction costs in portfolio optimization

- **ResearchGate:** https://www.researchgate.net/publication/286443114_A_Robust_Perspective_on_Transaction_Costs_in_Portfolio_Optimization

Read for cost-aware allocation and the relationship between turnover and estimation uncertainty.

### F. Risk-factor aggregation and stress testing

- **Publisher paper:** https://doi.org/10.1080/14697688.2024.2377735

Read for factor aggregation and stress-testing design.

## 26.2 Tail risk and performance validation

### G. Rockafellar and Uryasev — Conditional Value-at-Risk for General Loss Distributions

- **Publisher DOI:** https://doi.org/10.1016/S0378-4266(02)00271-6
- **Author publication list:** https://uryasev.github.io/publications/

Read for CVaR definitions and optimization formulations.

### H. Probability of Backtest Overfitting

- **Publisher DOI:** https://doi.org/10.21314/JCF.2016.322
- **Academic repository with PDF access:** https://escholarship.org/uc/item/4w1110bb

Read for selection bias and combinatorially symmetric cross-validation.

### I. Deflated Sharpe Ratio

- **Publisher DOI:** https://doi.org/10.3905/jpm.2014.40.5.094
- **Direct PDF:** https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf

Read for multiple testing, non-normal returns, and Sharpe-ratio selection bias.

## 26.3 Options and product risk

### J. OCC — Characteristics and Risks of Standardized Options

- **Official page with current disclosure document:** https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document

Use the current version and applicable supplements. The document covers standardized option characteristics and risks. Do not rely solely on an educational Black–Scholes formula to understand assignment, settlement, exercise, or margin.

### K. NSE — SPAN and options margin context

- **Official resource:** https://www.nseindia.com/static/products-services/equity-derivatives-nse-clearing-span

Use only where relevant to the venue and product. Margin methodologies vary by venue and should not be copied into a different market without validation.

## 26.4 Software implementation

### L. CVXPY quadratic programming

- **Official documentation:** https://cvxpy.readthedocs.io/en/functions-table/examples/basic/quadratic_program.html

Use for understanding convex quadratic programming, constraints, solver results, and dual variables.

### M. PyPortfolioOpt risk models

- **Official documentation:** https://pyportfolioopt.readthedocs.io/en/latest/RiskModels.html
- **Repository:** https://github.com/PyPortfolio/PyPortfolioOpt

Useful for covariance-estimation reference implementations. Evaluate suitability and version compatibility before adopting dependencies.

### N. FIA automated-trading safeguards

- **Direct PDF:** https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf
- **FIA article:** https://www.fia.org/fia/articles/fia-releases-best-practices-automated-trading-risk-controls-and-system-safeguards

Read for pre-trade controls, post-trade analysis, and system safeguards.

### O. SEC market-access controls

- **Official guidance:** https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0

Use as regulatory context where applicable. The legal requirements for AXEL depend on the entity, products, and jurisdiction.

---

# 27. Mathematical formula index

This index maps the main formula families to their implementation purpose.

| Formula family | Primary use | Section |
|---|---|---|
| Market value and FX conversion | Position valuation | 6, 8 |
| Simple and log returns | Return construction | 9 |
| Mean, variance, covariance | Statistical estimation | 9 |
| Correlation | Co-movement | 9 |
| Exponential covariance | Time-varying risk | 9 |
| Covariance shrinkage | Estimation robustness | 9 |
| Portfolio variance and volatility | Risk estimation | 10 |
| Marginal/component risk contribution | Risk attribution | 10 |
| VaR and expected shortfall | Tail risk | 10 |
| Drawdown | Loss-path analysis | 10 |
| Concentration index | Exposure concentration | 10 |
| Factor model | Shared risk | 7, 8 |
| Risk-budgeting objective | Portfolio risk allocation | 11 |
| Mean-variance objective | Allocation optimization | 12 |
| Robust optimization | Parameter uncertainty | 12, 16 |
| Quadratic program | Constrained optimization | 12 |
| Turnover and transaction costs | Implementation cost | 13 |
| Market impact approximation | Liquidity and costs | 13 |
| Black–Scholes and Greeks | Options valuation and sensitivity | 14 |
| Currency exposure | FX risk | 15 |
| Expected event payoff | Prediction contracts | 15 |
| Scenario loss | Stress testing | 16 |
| Sharpe, Sortino, Calmar | Performance measurement | 19 |
| Tracking error | Benchmark-relative risk | 19 |
| Point-in-time constraint | Leakage prevention | 19 |

---

# 28. Final engineering directive

Implement Phase 11 as a portfolio intelligence and proposal service, not as a second execution engine.

The required order is:

1. Establish trustworthy state.
2. Reconcile positions and commitments.
3. Normalize instruments and exposures.
4. Estimate risk.
5. Allocate risk capacity.
6. Generate candidate portfolios.
7. Account for costs and liquidity.
8. Stress-test proposals.
9. Persist explanations and audit evidence.
10. Submit proposals through the existing deterministic authorization boundary.
11. Validate in shadow mode.
12. Request release approval only after the evidence meets pre-approved criteria.

**Hard prohibition:** Do not change the deterministic core, risk policy, execution authorization, or independent Watchdog merely to make Phase 11 easier to implement. If an interface is missing, propose a reviewed interface change.

**Mathematical caveat:** This document contains the principal formula families needed to design the Phase 11 portfolio layer, but no finite specification can contain every formula needed for every instrument, venue, accounting regime, or future model. Each supported product requires verified payoff, valuation, margin, settlement, and risk conventions. Formulas must be selected according to the actual products AXEL trades and validated against independent test cases.

**Completion standard:** Phase 11 is ready for controlled integration only when its data is reconciled, its calculations are tested, its constraints are independently enforced, its results are reproducible, its costs are realistic, its stress tests are recorded, and its proposals cannot bypass the existing safety architecture.

End of specification.

