# AXEL v2 --- Phase 9 Product and Research Implementation Specification

## Bets and Prediction Markets: Probability Forecasting, Price Comparison, Calibration, Portfolio Sizing and Settlement

**Purpose:** Detect whether AXEL's forecasts provide a repeatable,
out-of-sample edge over available market prices after margin,
commission, limits and execution uncertainty. No strategy can guarantee
winning. Default is research and paper simulation; any real-money use
requires lawful access and explicit approval.

## 0. Non-negotiable rules

1.  Calibration and uncertainty matter because stake sizing magnifies
    probability errors.
2.  Accuracy/hit rate is not the objective. Track log loss, Brier score,
    calibration, market-relative performance and realized net returns.
3.  Odds are prices, not truth. De-vig and benchmark against more than
    one source when data rights permit.
4.  Positive estimated EV is not sufficient; subtract commission, costs,
    price movement and execution failure, and use conservative
    probability estimates.
5.  Fractional Kelly is a candidate, not a command. No full Kelly by
    default.
6.  No martingale, loss-chasing, doubling after losses or LLM override
    of bankroll limits.
7.  Correlation includes multiple markets on the same event and shared
    teams, players, news or settlement sources.
8.  Unknown event identity, stale odds, suspended markets, missing
    settlement rules or ledger mismatch means no new position.
9.  Respect law, age/eligibility, operator terms, market restrictions
    and responsible-gambling controls. No bypassing limits, geofencing
    or platform protections.
10. LLMs propose; deterministic services recompute
    probabilities/EV/stake/exposure and decide. Independent Watchdog can
    halt activity.

## 1. Research review: source findings and AXEL implications

### R9-1. Machine Learning for Sports Betting: Should Forecasting Models Be Optimised for Accuracy or Calibration? --- Walsh and Joshi

ResearchGate:
https://www.researchgate.net/publication/369184023_Machine_learning_for_sports_betting_should_forecasting_models_be_optimised_for_accuracy_or_calibration\
SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4705918\
The authors report that model selection based on calibration yielded
better simulated returns than selection based on accuracy in their
setup. **AXEL implication:** use calibrated probability vectors and
reliability diagrams; do not use the paper's ROI as an AXEL forecast.

### R9-2. Exploiting Sports-Betting Market Using Machine Learning --- Hubáček, Šourek and Železný (2019)

ResearchGate:
https://www.researchgate.net/publication/331218530_Exploiting_sports-betting_market_using_machine_learning\
The work studies forecasting that accounts for the relationship between
model predictions and bookmaker odds. **AXEL implication:** compare
models with market-implied baselines and test incremental value. Lower
correlation with bookmaker predictions alone does not establish an edge.

### R9-3. On Determining Probability Forecasts from Betting Odds --- Štrumbelj (2014)

ResearchGate:
https://www.researchgate.net/publication/264349990_On_determining_probability_forecasts_from_betting_odds\
DOI: https://doi.org/10.1016/j.ijforecast.2014.02.008\
The paper reports Shin-model probabilities can outperform basic
normalization in some settings and that differences vary by market size
and bookmaker. **AXEL implication:** implement and compare multiple
de-vig transforms by market segment; preserve raw odds.

### R9-4. Betting Market Efficiency and Prediction in Binary Choice Models --- Koning and Zijm (2022)

ResearchGate:
https://www.researchgate.net/publication/360268497_Betting_market_efficiency_and_prediction_in_binary_choice_models\
DOI: https://doi.org/10.1007/s10479-022-04722-3\
The study compares normalized and Shin probabilities in soccer markets
and reports differences by league. **AXEL implication:** do not hardcode
one universal margin transform.

### R9-5. Calibration and Hyperparameter Tuning in Football Forecasting with Machine Learning --- Wheatcroft and Sienkiewicz (2021)

ResearchGate:
https://www.researchgate.net/publication/352846677_Calibration_and_hyperparameter_tuning_in_football_forecasting_with_Machine_Learning\
The authors report calibration can help or hurt depending on model/data
and recommend evaluating probabilistic forecasts and decision utility.
**AXEL implication:** compare calibrated and uncalibrated models on
chronological data; fit calibrators only on training/validation periods.

### R9-6. Optimal Sports Betting Strategies in Practice: An Experimental Review

ResearchGate:
https://www.researchgate.net/publication/349139435_Optimal_sports_betting_strategies_in_practice_an_experimental_review\
The review evaluates bankroll allocation strategies and highlights the
practical importance of risk-control modifications to idealized
Kelly/portfolio methods. **AXEL implication:** compare flat and
conservative fractional-Kelly candidates with correlation and model
error included. Never assume probabilities are known exactly.

### R9-7. Explaining Variance in the Accuracy of Prediction Markets --- Strijbis and Arnesen

ResearchGate:
https://www.researchgate.net/publication/328646020_Explaining_variance_in_the_accuracy_of_prediction_markets\
DOI: https://doi.org/10.1016/j.ijforecast.2018.04.009\
The paper studies real-money prediction market errors and finds market
setup contributes to accuracy variation in its sample. **AXEL
implication:** track venue structure, liquidity, spread, rules and event
type; prices are not equally informative everywhere.

### R9-8. Scikit-learn calibration documentation

https://scikit-learn.org/stable/modules/calibration.html\
https://scikit-learn.org/stable/modules/generated/sklearn.calibration.calibration_curve.html\
Technical reference for reliability diagrams and calibration evaluation;
use alongside proper scoring rules and sample counts.

**Evidence caveat:** ResearchGate may expose an abstract/preprint rather
than full text. Findings are dataset-specific. Store access level,
source version, experiment IDs and every tested model/market/staking
configuration.

## 2. Scope and non-goals

**In scope:** legal sports markets and well-defined prediction markets;
odds/event snapshots; event identity; market rules; features available
at prediction time; multi-outcome forecasts; implied probabilities;
calibration; uncertainty; EV; conservative allocation; correlated
exposure; paper execution; settlement and ledger reconciliation.

**Out of scope initially:** automated in-play betting before
latency/suspension handling is validated; unauthorized scraping;
bypassing account restrictions; ambiguous settlement markets;
martingale; uncontrolled accumulators; automatic real-money execution by
default.

## 3. Architecture

``` text
Approved odds/event/statistics feeds
  -> immutable snapshots + normalized event/market store
  -> identity resolution + rules/status validation
  -> point-in-time feature pipeline
  -> probability models + market-implied baselines
  -> calibration + uncertainty + model registry
  -> deterministic value engine (EV, costs, price validity)
  -> portfolio allocator + bankroll risk gate
  -> paper bet / explicitly approved execution adapter
  -> rules-based settlement + result reconciliation
  -> metrics, calibration, CLV, audit and Watchdog
```

The agent is not the source of truth for odds, forecasts, stake, orders
or settlement. Numeric fields are independently recomputed by
deterministic services.

## 4. Event and market eligibility

Create a versioned registry for sport/event, market type, outcome set,
odds format, payout rules, commission,
void/push/dead-heat/postponement/overtime rules, market status,
settlement authority, close time, liquidity/limits and legal/venue
eligibility. Reject when outcomes are not coherent, rules changed
without versioning, providers refer to different events, market is
suspended/closed, or settlement is ambiguous.

## 5. Odds normalization and market baseline

For decimal odds `o_i`, raw implied probability is `q_i = 1 / o_i`.
Booksum is `Q = Σ q_i`; overround is commonly `Q - 1`, subject to the
market's payout conventions. Implement: 1. Raw inverse odds. 2.
Proportional normalization `p_i = q_i / Σq_i`. 3. Shin transform where
assumptions/data support it. 4. Optional empirical transform trained
only on past data and evaluated chronologically. Keep raw odds and all
transforms. Evaluate calibration by sport, league, market size, odds
band, provider and time-to-start. For exchanges, model commission on net
winnings and liquidity separately.

## 6. Probability models

Baselines first: - Market-implied probabilities under multiple de-vig
methods. - Simple frequency/strength model. - Regularized
logistic/multinomial logistic regression. - Elo or domain-appropriate
rating baseline. - Gradient-boosted trees only after baseline pipeline
is reliable. - More complex neural/sequence models only if sample size
and validation justify them.

Candidate features, only if available at prediction time: - Odds and
movements with provider/status/timestamps. - Team/player strength
ratings computed as-of the event. - Historical form with
opponent-strength adjustment and decay. - Rest, travel, venue, schedule
congestion. - Confirmed lineups/injuries only after publication
timestamp. - Weather and sport-specific stats where relevant and
licensed. - Liquidity, spread, time-to-start, data-quality flags.

Each feature records `event_time`, `available_at`, `ingested_at`, source
and version. Anything not known at forecast time is prohibited.

Forecast output must include probability vector, forecast
timestamp/expiry, model and training cutoff, calibrator version,
uncertainty, data snapshot IDs, missingness and out-of-distribution
flags.

## 7. Calibration and evaluation

Metrics: - Binary/multiclass log loss and Brier score. - Reliability
diagrams, probability-bin counts and uncertainty. - Calibration
slope/intercept where suitable. - Ranking metrics as secondary
diagnostics. - Net EV/return after costs and CLV where comparable close
prices exist. - Metrics by sport, league, market, odds band and forecast
horizon.

Compare no-calibration baseline with Platt/logistic calibration,
isotonic only with enough data, temperature scaling for suitable
multiclass models, and empirical mappings only with adequate segment
samples. Fit calibrators on time-separated data and freeze before final
holdout. Calibration does not replace discrimination; report both.

Required comparisons: model vs market baseline; calibrated vs
uncalibrated; features with/without odds; provider vs consensus
benchmark; strategy vs no-bet; flat vs fractional Kelly under identical
forecasts; delayed/worse prices and missed bets.

## 8. Value engine

For a simple win/loss bet at decimal odds `o`, estimated win probability
`p`, stake `s`, and no additional costs:
`EV = p * s * (o - 1) - (1 - p) * s` This is not valid unchanged for
commission, pushes, voids, dead heats, cash-outs or complex prediction
contracts; implement a settlement-specific payout function.

For each candidate: - Use the current available price, not stale odds. -
Subtract commission/fees and execution costs. - Account for probability
uncertainty and forecast decay. - Include chance of price/market
becoming unavailable. - Calculate conservative EV using a lower
confidence bound or uncertainty haircut. - Require an edge threshold
above costs and uncertainty; threshold is versioned per market type. -
Reject if suspended or price has moved beyond tolerance. - Positive
point-estimate EV is not a guaranteed edge.

## 9. Bankroll and portfolio sizing

For a simple two-outcome decimal-odds wager, net profit per unit stake
if won is `b = o - 1`. Idealized full Kelly fraction:
`f* = (b*p - (1-p)) / b` Clamp negative stake to zero. This assumes
correctly specified probabilities/payouts and does not handle model
error, correlation, limits or complex settlement. AXEL must not default
to full Kelly.

Compare flat small stake, conservative fractional Kelly, and
portfolio-aware allocation. Enforce max stake per
bet/event/market/day/rolling period; max correlated exposure;
daily/rolling drawdown; reserve bankroll; no borrowing by default; no
stake increases after losses; probability shrinkage when calibration
drifts; no bet if edge is within uncertainty; no stake if odds, bankroll
or open bets cannot be reconciled.

## 10. Correlation, CLV and settlement

Use an event graph for same event, team/player, player props,
totals/spreads, shared news, macro/political events and shared
settlement sources. Compute combined worst-case scenario exposure.
Opposing positions are not assumed to hedge unless payout math proves it
for all outcomes.

CLV record includes entry odds/time, close odds/time,
provider/rules/outcome mapping, transform and commission assumptions,
and sign convention. CLV is a diagnostic, not proof of profit.

Persist rules before accepting any paper bet. Settlement must handle
win/loss/draw, push, void, postponement, abandonment, overtime rules,
dead heats, partial payout, result corrections, and prediction-market
disputes/cancellations. Store authoritative source, retrieval time,
source payload/hash, rules version, payout and reconciliation status.
Ambiguous settlement =\> `SETTLEMENT_REVIEW_REQUIRED`.

## 11. Data contracts

-   `Event`: canonical ID, participants, start time, venue, sport,
    identity confidence.
-   `Market`: event, provider, market type, rules version, outcomes,
    status, fees, close time.
-   `OddsSnapshot`: market/outcome, raw odds, liquidity/limits, status,
    event/receive times.
-   `FeatureSnapshot`: values, `available_at`, source/version,
    missingness.
-   `Forecast`: probability vector, model/calibrator versions,
    uncertainty, snapshot IDs.
-   `ValueAssessment`: price, market baseline, EV/conservative EV,
    costs, threshold, expiry.
-   `BetProposal`: outcome, requested stake, price limit, forecast ID,
    rationale, valid-until.
-   `BetIntent`, `BetOrder`, `BetFill`, `OpenExposure`, `Settlement`,
    `BankrollLedger`, `CalibrationReport`, `ModelCard`, `ExperimentRun`,
    `AuditEvent`.

Conceptual API contracts (align actual paths with repo): forecast
ingestion; deterministic value assessment; proposal creation; risk
validation; paper bet submission; settlement reconciliation. A proposal
endpoint never places a bet.

## 12. Packages and trust boundary

-   `P9-FEEDS`, `P9-IDENTITY`, `P9-RULES`, `P9-ODDS`, `P9-FEATURES`
-   `P9-MODELS`, `P9-CALIBRATION`, `P9-VALUE`, `P9-SIZING`,
    `P9-EXPOSURE`
-   `P9-PAPER`, `P9-SETTLE`, `P9-LEDGER`, `P9-AGENTS`, `P9-OBS`,
    `P9-WATCHDOG`, `P9-QA`

Deterministic gate must: validate event/market identity; confirm
open/fresh price; load frozen forecast/calibrator; recompute implied
probabilities and EV using current price/rules; apply uncertainty
haircut; recompute stake and correlated exposure; check bankroll,
provider status and permissions; emit stable reason codes; persist
inputs/decision/config hash; require explicit approval for real money.
Agents cannot write bankroll, orders, fills, settlements or risk
settings.

## 13. Test plan

**Odds/probability:** inverse odds, de-vig, Shin edge cases, valid
finite probability vectors, commission/payout math, EV break-even,
stale-price invalidation.\
**Leakage:** feature timestamp precedes forecast, no future
results/final lineups/closing odds, provider event mismatch detection,
suspended markets blocked, duplicate events idempotent.\
**Models:** chronological splits, untouched holdout, calibration sample
counts, calibrator fit isolation, market baseline comparisons, drift/OOD
detection.\
**Portfolio:** same-event exposures aggregate, daily limits block,
negative Kelly becomes no-bet, production cannot select full Kelly,
ledger reconciles, no martingale stake sequences.\
**Settlement/fault:** win/loss/draw/push/void/dead
heat/postponement/correction; provider timeout; duplicate submission;
contradictory result; ledger mismatch; Watchdog independent halt; agent
permission tests.

## 14. Promotion gates

**A Data integrity:** versioned rules, snapshots, identity and leakage
tests.\
**B Forecast quality:** baselines, calibration reports, out-of-sample
improvement/useful complement to market baseline, segment stability and
uncertainty documented.\
**C Value simulation:** commissions and realistic price availability;
no-bet baseline; multiple-testing registry; correlation/drawdown gates.\
**D Paper soak:** execution-time prices, independently reconciled
settlement, no unexplained P&L, drift/failure/halt drills.\
**E Real-money eligibility:** human legal/venue/account checks, separate
approval, small caps, no deploy-time activation, Watchdog and rollback
drill. Small samples must not be presented as proof.

## 15. Monitoring

Track log loss, Brier, calibration by segment, forecast/abstention/OOD
rates, estimated vs realized EV, CLV, accepted/rejected/voided/settled
counts, net return/drawdown/exposure, odds age/feed latency, settlement
mismatches, model drift, bankroll reconciliation and halt events.

## 16. Definition of done

Market/rules are versioned; forecasts are calibrated and chronologically
evaluated; EV includes costs and execution uncertainty; stake sizing is
deterministic and portfolio-aware; settlement reconciles to immutable
ledger; Watchdog can halt independently; agents cannot bet or alter
risk; paper gate evidence is reviewed; no deployment automatically
enables real-money betting.

## 17. OpenCode kickoff prompt

> Inspect the actual AXEL repository before editing; report existing
> modules, feeds, APIs, migrations, tests and safety boundaries. Propose
> a file-level plan. Begin with event/market identity, rules, odds
> snapshots, point-in-time features and baselines. The deterministic
> core independently calculates probabilities, EV, stake and correlated
> exposure. LLMs only create proposals/commentary. Do not implement
> martingale, full-Kelly production sizing, unauthorized data access or
> automatic real-money execution. Test leakage, calibration, stale
> prices, market status, settlement, duplicate submission, bankroll
> reconciliation and Watchdog behavior. Fail closed on missing rules and
> never weaken existing risk gates.
