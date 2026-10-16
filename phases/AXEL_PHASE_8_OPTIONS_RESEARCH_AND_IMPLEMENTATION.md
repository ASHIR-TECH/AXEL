# AXEL v2 --- Phase 8 Product and Research Implementation Specification

## Options Section: Volatility, Defined-Risk Structures, Execution Quality and Controlled Promotion

**Purpose:** Build a reproducible options research/trading system that
searches for repeatable, out-of-sample edge after realistic costs. This
is a research and engineering specification, not a promise of profit.
**No strategy is a guaranteed winner.**

## 0. Hard constraints

1.  LLMs propose; deterministic core validates, sizes, vetoes and
    authorizes. No LLM-to-broker calls.
2.  Initial scope is defined-risk options only. No naked/uncovered short
    options, martingale, automatic leverage or unbounded exposure.
3.  No midpoint-only backtests. Model spread, fees, slippage, partial
    fills, market impact, multi-leg execution and order latency.
4.  No look-ahead or survivorship leakage. Every feature has an
    availability timestamp and lineage.
5.  Missing/stale quotes, uncertain contract terms, broken calendars,
    unresolved orders or failed reconciliation means fail closed.
6.  Independent Watchdog can halt new orders without the agent service
    being online.
7.  Research success does not enable live trading. Paper and live
    promotion are separate, human-approved gates.

## 1. Research review: what sources support

### R8-1. Volatility Risk Premium in Indian Options Prices --- Garg and Vipul (2014)

ResearchGate:
https://www.researchgate.net/publication/262269835_Volatility_Risk_Premium_in_Indian_Options_Prices\
DOI: https://doi.org/10.1002/fut.21680\
The paper compares implied and realized volatility forecasts and reports
a volatility risk premium, but states strategy returns shrink
substantially after normal transaction costs. Its ResearchGate page
indicates no full text available there. **AXEL implication:** VRP is a
hypothesis, not a default profit engine. Include spread, fees, slippage,
hedging and venue-specific costs; replicate on AXEL's target products.

### R8-2. Very Noisy Option Prices and Inference Regarding the Volatility Risk Premium --- Duarte, Jones and Wang (Journal of Finance, 2024)

ResearchGate:
https://www.researchgate.net/publication/383895176_Very_Noisy_Option_Prices_and_Inference_Regarding_the_Volatility_Risk_Premium\
DOI: https://doi.org/10.1111/jofi.13365\
The study highlights that noisy option prices and microstructure biases
can materially change inferences about volatility risk premia. **AXEL
implication:** quote-quality filters, multiple mark conventions, robust
IV calculation, uncertainty flags and sensitivity tests are mandatory.

### R8-3. The Impact of Hedging Costs on the Bid and Ask Spread in the Options Market

ResearchGate:
https://www.researchgate.net/publication/228818078_The_Impact_of_Hedging_Costs_on_the_Bid_and_Ask_Spread_in_the_Options_Market\
The paper treats hedging cost as an important component of options
spreads, including initial delta hedge and gamma-related rebalancing
costs. **AXEL implication:** do not model dynamic hedging as free.
Include underlying spread, fees, latency and rebalance frequency, or
prefer defined-risk structures that avoid continuous hedging in v1.

### R8-4. On Optimal Options Book Execution Strategies with Market Impact --- Kalife and Mouti (2017)

ResearchGate:
https://www.researchgate.net/publication/317192643_On_Optimal_Options_Book_Execution_Strategies_with_Market_Impact\
DOI: https://doi.org/10.1142/S2382626617500022\
The work studies execution of an options book under market impact.
**AXEL implication:** separate signal alpha from execution quality; use
conservative limit-order and timeout policies. The paper does not prove
any particular AXEL execution algorithm profitable.

### R8-5. SEC Investor Bulletin: An Introduction to Options (updated July 2026)

https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-63\
The SEC explains option rights/obligations, exercise, assignment, the
possibility of losing the entire premium on long options, and
potentially much greater losses for option writers. **AXEL
implication:** exercise style, settlement, multiplier, deliverable,
expiry cutoffs, dividends, corporate actions and assignment behavior are
required risk inputs.

### R8-6. Cboe Options Institute

https://www.cboe.com/oi/tools/\
https://www.cboe.com/optionsinstitute/defining-options\
Use as operational/educational references for terminology and sanity
checks, not as evidence of alpha.

**Evidence caveat:** ResearchGate may expose only abstracts or previews.
Record access level and never claim AXEL replicated a result until its
own reproducible experiment proves it.

## 2. Scope and non-goals

**In scope:** equity/index option chain data; contract metadata;
underlying quotes/bars; IV/Greeks; volatility surface, skew, term
structure and liquidity features; defined-risk strategy builders;
realistic backtesting; paper trading; assignment/expiry handling; broker
state machine; reconciliation; audit and monitoring.

**Out of scope initially:** uncovered short options, unvalidated
HFT/market-making, automatic exercise decisions, unsupported contracts,
synthetic quotes treated as executable, and live activation by
deployment.

## 3. Architecture

``` text
Market data + contract metadata + calendars + corporate actions
  -> immutable raw event store / normalized TimescaleDB
  -> point-in-time data-quality and feature pipeline
  -> IV / Greeks / surface / liquidity service
  -> research models and strategy evaluator
  -> Options Analyst(s) -> Expert Panel -> Section Manager
  -> typed OptionsProposal (untrusted)
  -> deterministic contract/payoff/risk/portfolio validator
  -> approval gate -> order manager -> broker adapter
  -> fills/positions/assignment/expiry -> reconciliation ledger
  -> dashboard, audit, alerts and independent Watchdog
```

Agents may write proposals and research artifacts only. They must not
write orders, fills, positions, risk configuration or approvals.

## 4. Strategy hypotheses to research --- none is pre-certified as a winner

### S8-A: Volatility risk premium

Estimate implied variance using a documented point-in-time method;
compare with forecast realized variance from simple historical/EWMA and
justified GARCH/ML baselines. Features may include term structure, skew,
volatility regime, event calendar, liquidity and uncertainty. Test only
bounded-risk structures such as vertical credit spreads initially.
Reject if quote quality is weak, spreads are wide, tail loss exceeds
policy, or edge disappears under realistic costs.

### S8-B: Realized-volatility forecasting

Compare simple baselines before tree-based ML. Score forecasts
independently from trading P&L; good volatility prediction does not
automatically mean profitable option selection.

### S8-C: Surface/relative-value anomalies

Use synchronized underlying and option quotes. A surface anomaly is not
automatically an arbitrage. Include dividends, borrow, early exercise,
bid/ask bounds, model uncertainty and legging risk.

### S8-D: Defined-risk directional spreads

Candidate structures: debit call/put spreads and credit verticals.
Choose using a forecast distribution, executable entry/exit prices,
bounded payoff and portfolio risk. No naked legs in v1.

### S8-E: Event volatility

Earnings and macro events require timestamped calendars and
point-in-time knowledge. Separate implied move, realized move, gap risk
and liquidity changes. Exclude events when information availability
cannot be reconstructed.

## 5. Data contracts and quality rules

Required entities: - `UnderlyingQuote`: instrument, event/receive time,
bid/ask, sizes, venue, condition, sequence. - `OptionContract`: stable
ID, underlying, put/call, strike, expiry, multiplier, exercise style,
settlement, currency, deliverable, venue, last-trading and exercise
cutoffs. - `OptionQuote` / `OptionTrade`: contract ID, event/receive
time, bid/ask/sizes or trade price/size, venue, condition. -
`OpenInterest`: as-of time and known-at time; never pretend daily OI was
known intraday. - `CorporateAction`, `MarketCalendar`,
`IVGreekSnapshot`, `OptionsProposal`, `OptionsOrder`, `OptionsFill`,
`OptionsPosition`, `AssignmentEvent`, `ExpiryEvent`,
`ReconciliationRun`.

Invariants: - Keep raw events immutable; store event time and ingest
time; normalize internally to UTC and retain venue-local session. -
Reject crossed, stale, invalid, or contract-mismatched quotes. - Support
adjusted deliverables, splits, special dividends, mergers and symbol
changes. - Persist price-mark convention, data source, schema version,
quality flags and snapshot ID. - Never fill missing contract metadata
with guesses.

## 6. Pricing and Greeks

-   Select a pricing model appropriate to exercise/settlement type;
    document model and conventions.
-   Compute IV only from validated marks; record solver convergence and
    bounds.
-   Record Greek units and model version.
-   Keep observed bid/ask, theoretical value, fitted surface value and
    order limit separate.
-   For American-style options, do not blindly apply European
    assumptions; account for early exercise and dividends.
-   Flag rather than silently smooth no-arbitrage violations or surface
    extrapolation.

Suggested quality flags: `VALID`, `STALE`, `CROSSED`, `WIDE_SPREAD`,
`LOW_DEPTH`, `IV_SOLVER_FAIL`, `CORPORATE_ACTION_UNCERTAIN`,
`CALENDAR_UNCERTAIN`, `SURFACE_EXTRAPOLATION`, `SYNTHETIC_ONLY`.

## 7. Deterministic payoff and portfolio risk

For each proposed structure: - Validate legs, expiry, multiplier,
currency and settlement compatibility. - Calculate net debit/credit,
payoff grid, worst-case loss, best-case gain, break-even points and
margin buffer. - Stress underlying gaps, IV/skew shifts, time decay,
spread widening and liquidity disappearance. - Include existing
positions, working orders, partial fills, pending cancels and unknown
order states. - Enforce per-trade max loss, underlying/expiry/sector
concentration, total options exposure and cross-section portfolio
limits. - No order if max loss cannot be calculated or risk is not
bounded under supported contract assumptions. - No risk increase based
only on LLM confidence; no doubling after losses.

Risk values must be versioned configuration, not universal constants
hardcoded into code. Any change requires authorization, audit and
regression tests.

## 8. Backtesting requirements

Model bid/ask, order direction, contract fees, exchange fees, slippage,
size/depth, partial fills, multi-leg legging risk, order latency,
underlying hedge costs, trading sessions, halts, early closes,
assignment, exercise, expiration, dividends, settlement and adjusted
deliverables. Do not assume fills outside eligible hours or on stale
quotes.

Validation: 1. Lock universe/eligibility before holdout inspection. 2.
Use chronological walk-forward; purge/embargo overlapping labels where
needed. 3. Keep an untouched final holdout. 4. Record all
strategy/parameter/model trials for multiple-testing accounting. 5.
Report net P&L, expectancy, return on risk, drawdown, tail loss,
turnover, fill ratio, spread/fee share and Greeks exposure by regime. 6.
Stress worse fills/costs and perturb parameters. 7. Compare with
no-trade and simple rules-only baselines. 8. Use confidence intervals
that respect temporal dependence. 9. The project's 100 OOS trades is a
minimum floor, not proof of sufficiency for rare-event strategies.

Required artifacts: data-quality report, strategy/model cards, frozen
config and data hashes, reproducible backtest report, multiple-testing
disclosure, sensitivity report, portfolio stress report and paper-soak
report.

## 9. Order, assignment and settlement lifecycle

Suggested states:
`PROPOSED -> VALIDATED -> AWAITING_APPROVAL -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED`,
with `REJECTED`, `CANCEL_REQUESTED`, `CANCELLED`, `EXPIRED`, `UNKNOWN`,
`RECONCILIATION_REQUIRED`. - Persist intent before submit; use
idempotency keys. - Broker state is authoritative; `UNKNOWN` blocks
duplicate submission and new risk until reconciled. - Handle
cancel/replace races and partial fills explicitly. - Use
broker-supported complex orders where possible; otherwise reject or use
a separately approved legging policy. - Maintain venue-specific
exercise/expiry cutoffs, early assignment monitoring, dividend risk and
settlement cash-flow simulation. - No silent exercise action. If
assignment, contract terms or position state is uncertain, halt new
orders and escalate.

## 10. Proposed implementation packages

-   `P8-DATA`: chain/reference adapters, raw capture, quality checks.
-   `P8-CALENDAR`: expiry, market sessions, corporate actions.
-   `P8-PRICING`: IV, Greeks, surface snapshots.
-   `P8-FEATURES`: point-in-time volatility, skew, term structure,
    liquidity.
-   `P8-RESEARCH`: baselines, experiments, model cards.
-   `P8-STRATEGY`: defined-risk candidate builders and payoff engine.
-   `P8-RISK`: deterministic eligibility, scenario and portfolio risk.
-   `P8-BACKTEST`: fill/cost/assignment/expiry-aware simulation.
-   `P8-EXEC`: broker adapter and order state machine, only after Phase
    6 interfaces are audited.
-   `P8-LEDGER`: positions, cash, fills, assignment, reconciliation.
-   `P8-AGENTS`: proposal-only analysts/panel/section manager.
-   `P8-OBS`, `P8-WATCHDOG`, `P8-QA`: metrics, independent halt,
    contract/fault/replay tests.

`OptionsProposal` minimum: `proposal_id`, `strategy_id`,
`underlying_id`, `legs[]`, `created_at`, `valid_until`,
`forecast_version`, `data_snapshot_id`, `limit_policy`, `max_loss`,
`max_gain`, `break_even_points[]`, `greeks`, `liquidity_snapshot`,
`stress_summary`, `thesis`, `uncertainty`, `agent_trace_id`.

Deterministic validator returns `ACCEPT`, `REJECT` or `REQUIRES_REVIEW`
with stable reason codes. It must not silently rewrite the proposal.

## 11. Security and test requirements

-   Agent DB role can insert proposals/research only; execution services
    own order/fill/position tables.
-   Watchdog uses separate credentials and can halt new orders.
-   Secrets never enter prompts/logs/notebooks; least-privilege broker
    credentials.
-   CI prevents LLM/provider imports in deterministic core and broker
    calls from agent packages.
-   Unit/property tests: payoff bounds, multiplier, adjusted
    deliverable, IV convergence, Greek units, risk aggregation, stale
    quote veto, calendar/DST, cost accounting.
-   Fault tests: feed disconnect, out-of-order/duplicate quote, missing
    metadata, broker timeout after submit, duplicate ack, partial fill,
    cancel race, assignment, expiry, corporate action, reconciliation
    mismatch, Watchdog halt.
-   Replay must reproduce why a proposal was accepted/rejected from
    persisted inputs.

## 12. Promotion gates

**A Research:** clean data, reproducible tests, realistic costs.\
**B OOS:** untouched holdout, robust costs/parameters, documented tail
risks and multiple-testing control.\
**C Paper:** pre-registered duration/sample, reconciled positions/cash,
no critical incident or unexplained P&L.\
**D Live eligibility:** Phase 5/6 gates, human sign-off,
jurisdiction/broker permission, explicit small limits, kill-switch drill
and separate operator activation. Deployment never activates live
trading.

## 13. Definition of done

Phase 8 is not complete because strategy code exists. Interfaces,
migrations, contract/fault tests, proposal-only permissions,
deterministic risk, Watchdog, realistic backtests,
assignment/expiry/corporate-action reconciliation, dashboards, alerts,
runbooks and paper evidence must all be reviewed.

## 14. OpenCode kickoff prompt

> Inspect the real AXEL repository before editing and report existing
> modules, interfaces, migrations, tests and safety boundaries. Propose
> a file-level plan. Implement this spec in small packages. Do not
> assume a phase is complete because a folder exists. Keep LLMs
> proposal-only; no uncovered short options or automatic live
> activation. Add typed contracts, audit events, migrations, tests and
> rollback notes. Fail closed on unknown broker, contract, calendar,
> assignment, cost or data semantics. Never weaken existing risk limits
> to make tests pass.
