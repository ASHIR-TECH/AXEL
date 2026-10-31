# AXEL — Developer 1 Technical Research and Implementation Specification

## Data Engineering, Quantitative Research and Asset Integrations

**Project:** AXEL
**Repository:** https://github.com/ASHIR-TECH/AXEL
**Owner:** Developer 1
**Primary responsibility:** Data, quantitative modelling, research validation and asset-specific integrations.
**Target phases:** 2, 7, 7b, 8, 9, 10 and quantitative support for 11.

---

# 1. MISSION AND RESPONSIBILITIES

The objective of this workstream is to build the quantitative and data foundation
that allows AXEL to analyse multiple asset classes using reproducible,
point-in-time, cost-aware and statistically defensible data.

This developer owns the research and data components that supply the rest
of AXEL with:

- Historical market data.
- Real-time market data.
- Normalised instrument representations.
- Point-in-time datasets.
- Market microstructure features.
- Quantitative features.
- Strategy research.
- Backtesting results.
- Experiment metadata.
- Asset-specific analytics.
- Portfolio risk inputs.
- Independent validation results.

The objective is not simply to connect APIs.

Every dataset and quantitative output must be traceable, reproducible,
timestamped and independently verifiable.

## 1.1 Fundamental engineering principles

1. Never use future information when constructing historical features.
2. Never assume historical data is complete.
3. Never treat a backtest as proof of profitability.
4. Never ignore transaction costs.
5. Never assume different exchanges use identical market conventions.
6. Never treat a model prediction as an executable order.
7. Never allow missing or stale data to silently become valid data.
8. Never overwrite historical experiment results.
9. Every experiment must identify its exact data and configuration versions.
10. Every asset adapter must satisfy a common interface.

## 1.2 System boundary

Developer 1 produces data, features, predictions, research outputs and
validated quantitative inputs.

Developer 2 owns agent orchestration, portfolio integration, production
execution and operational governance.

The interface must remain explicit.

The data and quant workstream must not bypass AXEL's deterministic risk
and execution controls.

---

# 2. PHASE 2 — DATA ENGINEERING AND BACKTESTER

## 2.1 Objectives

Implement a reliable data platform capable of supporting:

- Equities.
- Cryptocurrency.
- Forex.
- Options.
- Betting and prediction markets.
- Cross-asset portfolio research.

The platform must support historical research and real-time processing
without mixing their assumptions.

## 2.2 Recommended architecture

Suggested logical architecture:

    External Providers
          |
          v
    Provider Adapters
          |
          v
    Raw Immutable Storage
          |
          v
    Validation and Normalisation
          |
          v
    Point-in-Time Data Store
          |
          +------> Feature Engineering
          |
          +------> Backtesting
          |
          +------> Research Experiments
          |
          +------> Live Feature Streams

Recommended technologies:

| Component | Candidate technology |
|---|---|
| Primary relational store | PostgreSQL |
| Time-series support | TimescaleDB |
| Analytical storage | ClickHouse |
| Raw historical data | S3-compatible object storage |
| Streaming | Kafka or Redpanda |
| Cache | Redis |
| Data processing | Python, Polars, Pandas |
| Numerical computation | NumPy, SciPy |
| Model development | scikit-learn |
| Gradient boosting | LightGBM, XGBoost |
| Experiment tracking | MLflow or equivalent |

These are recommendations, not mandatory dependencies.
Benchmark them against the expected workload before final selection.

## 2.3 Data provider abstraction

Implement a common provider interface.

Suggested interface:

    class MarketDataProvider:
        async def get_instruments(self):
            ...

        async def get_historical_bars(
            self,
            instrument_id,
            start_time,
            end_time,
            interval
        ):
            ...

        async def get_trades(
            self,
            instrument_id,
            start_time,
            end_time
        ):
            ...

        async def get_order_book(self, instrument_id):
            ...

        async def get_funding_rates(self, instrument_id):
            ...

        async def health_check(self):
            ...

Not every provider supports every operation.

Unsupported operations must return an explicit capability error.
They must not return fabricated or empty data that looks valid.

## 2.4 Canonical instrument representation

All asset classes should have a common instrument identity.

Suggested fields:

- instrument_id
- asset_class
- provider_id
- provider_symbol
- base_currency
- quote_currency
- exchange
- venue
- contract_type
- tick_size
- lot_size
- minimum_quantity
- expiry
- strike
- option_type
- settlement_currency
- trading_calendar
- timezone
- metadata_version

Asset-specific fields may be optional.

A stock, option, currency pair and prediction-market contract
must not be treated as interchangeable instruments.

## 2.5 Point-in-time data

A critical requirement is separating:

- Event time: when something happened.
- Observation time: when the provider published it.
- Ingestion time: when AXEL received it.
- Availability time: when it could legitimately have been used.

Suggested schema:

    event_timestamp
    published_timestamp
    received_timestamp
    available_timestamp
    source_id
    instrument_id
    data_version

A historical strategy must only access information that was available
at the simulated decision timestamp.

### Example

Suppose a macroeconomic figure describes January but is published in February.

A strategy running in January must not have access to the February publication.

Using the January reference period alone is insufficient.
The actual publication timestamp must be respected.

## 2.6 Return calculations

Simple return:

    R_t = (P_t - P_(t-1)) / P_(t-1)

Log return:

    r_t = ln(P_t / P_(t-1))

Portfolio return:

    R_p,t = sum(w_i,t * R_i,t)

Where:

- P_t = price at time t
- w_i,t = portfolio weight
- R_i,t = asset return

Log returns are additive across consecutive periods:

    r_(1,T) = sum(r_t)

Use the return definition consistently.
Do not mix simple and log returns without an explicit conversion.

## 2.7 Backtesting requirements

The backtester must model:

- Commissions.
- Bid-ask spread.
- Slippage.
- Market impact.
- Funding.
- Borrow costs.
- Financing costs.
- Taxes where applicable.
- Contract expiry.
- Partial fills.
- Rejections.
- Trading sessions.
- Holidays.
- Corporate actions.
- Settlement rules.

Gross strategy return:

    R_gross = strategy return before costs

Net strategy return:

    R_net = R_gross - commissions - spread_cost
            - slippage - financing - other_costs

Execution assumptions must be documented.

A backtest using candle closing prices must not pretend that an order
was necessarily executable at that exact closing price.

## 2.8 Walk-forward validation

Use chronological validation.

Example:

    Training: 2020-2022
    Validation: 2023 Q1
    Testing: 2023 Q2

Then move the window forward.

The exact periods should depend on the asset's liquidity,
market regime and data availability.

Do not randomly shuffle financial time-series observations
unless the problem genuinely permits it.

### Purging and embargo

If labels overlap in time, remove training observations whose
information intervals overlap the validation interval.

Apply an embargo after validation periods where necessary.

The purpose is to prevent information leakage through overlapping
labels and dependent observations.

---

# 3. STATISTICAL VALIDATION AND OVERFITTING

## 3.1 Sharpe Ratio

The annualised Sharpe Ratio is:

    SR = sqrt(A) * mean(r - r_f) / std(r - r_f)

Where:

- A = annualisation factor
- r = periodic portfolio return
- r_f = periodic risk-free return

The annualisation factor must match the observation frequency.

Do not use a daily annualisation factor for hourly observations.

## 3.2 Maximum drawdown

Define the running peak:

    Peak_t = max(P_0, P_1, ..., P_t)

Drawdown:

    DD_t = (P_t - Peak_t) / Peak_t

Maximum drawdown:

    MDD = max(-DD_t)

Report both percentage and duration.

## 3.3 Probability of Backtest Overfitting

Reference:

Bailey, Borwein, López de Prado and Zhu,
"The Probability of Backtest Overfitting" (2017).

ResearchGate:
https://www.researchgate.net/publication/228721092_The_Probability_of_Backtest_Overfitting

DOI:
https://doi.org/10.21314/JCF.2016.322

The central problem is strategy selection bias.

If AXEL tests hundreds or thousands of parameter combinations,
the best historical result may simply be the result of searching
a large number of alternatives.

Implement Combinatorially Symmetric Cross-Validation (CSCV)
or an equivalent documented PBO procedure.

The PBO framework estimates how often an in-sample winner
becomes an out-of-sample loser.

The implementation must preserve:

- Experiment identity.
- Number of strategies tested.
- In-sample ranking.
- Out-of-sample ranking.
- Partition definitions.
- Return series.
- Calculation version.

Do not calculate PBO using an incomplete experiment registry.

## 3.4 Deflated Sharpe Ratio

The Deflated Sharpe Ratio adjusts for selection bias,
non-normality and multiple trials.

A simplified conceptual expression is:

    DSR = Prob(SR_observed > SR_benchmark)

The benchmark must account for the expected maximum Sharpe
from multiple trials.

The implementation should use the full statistical procedure
from the selected reference, not an improvised formula.

Record:

- Observed Sharpe.
- Number of independent trials or estimated effective trials.
- Skewness.
- Kurtosis.
- Sample length.
- Benchmark Sharpe.
- Probabilistic result.

Do not confuse a conventional Sharpe Ratio with a DSR.

---

# 4. PHASE 7 — CRYPTOCURRENCY INTEGRATION

## 4.1 Objectives

Implement cryptocurrency market-data ingestion and quantitative research.

Required capabilities:

- Spot market data.
- Perpetual futures data.
- Funding rates.
- Open interest.
- Trades.
- Order books.
- Liquidation data where available.
- Exchange status.
- Instrument metadata.
- Market depth.
- Historical candles.

## 4.2 Order-flow imbalance

Reference:

Eduard Silantyev (2019),
"Order Flow Analysis of Cryptocurrency Markets".

ResearchGate:
https://www.researchgate.net/publication/335736211_Order_Flow_Analysis_of_Cryptocurrency_Markets

The research investigates order-flow behaviour in cryptocurrency markets.

### Order-book imbalance

    OBI = (Q_bid - Q_ask) / (Q_bid + Q_ask)

Where:

- Q_bid = aggregate bid-side quantity
- Q_ask = aggregate ask-side quantity

The aggregation depth must be specified.

An imbalance calculated from the best bid and ask is not equivalent
to one calculated over ten price levels.

### Trade-flow imbalance

    TFI = sum(s_i * q_i)

Where:

- s_i = trade direction (+1 buy, -1 sell)
- q_i = trade quantity

Normalised:

    NTFI = sum(s_i * q_i) / (sum(q_i) + epsilon)

Trade direction must be based on provider aggressor-side information
or a documented classification algorithm.

Do not assume that every provider's buy/sell label has identical meaning.

## 4.3 Funding rates

Funding must be incorporated into perpetual-futures returns.

Approximate net return:

    R_net = R_price + R_funding - R_fees - R_slippage

Funding convention must be verified per exchange.

A funding payment may be positive or negative depending on
position direction and the exchange's convention.

## 4.4 Exchange risk

Implement:

- Exchange availability monitoring.
- API rate-limit handling.
- Timestamp drift checks.
- Duplicate trade detection.
- Sequence-gap detection.
- Order-book reconstruction checks.
- Provider failover.
- Venue-specific symbol mapping.

Never merge two exchange order books as if they represented
one executable market.

## 4.5 Crypto acceptance criteria

- Historical and live schemas match.
- Funding calculations reconcile.
- Exchange timestamps are preserved.
- Order-book sequence gaps are detected.
- Missing data blocks dependent features.
- Duplicate events are idempotently handled.
- Market-data adapters pass independent contract tests.
- No live leverage is enabled by this phase alone.

---

# 5. PHASE 7b — FOREX

## 5.1 Objectives

Implement currency-pair data and quantitative analysis.

Required data:

- Bid and ask prices.
- Mid-price.
- Trade ticks where available.
- Interest rates.
- Rollover rates.
- Economic calendar events.
- Market sessions.
- Broker execution costs.
- Liquidity information.

Potential providers include OANDA, Interactive Brokers
and other documented institutional or retail data providers.

Provider selection must consider licensing, availability,
rate limits and permitted use.

## 5.2 Carry return

Under a convention where a positive rate differential
benefits the long position:

    R_carry ≈ (i_base - i_quote) * Delta_t + FX_return

Where:

- i_base = base currency interest rate
- i_quote = quote currency interest rate
- Delta_t = holding period expressed in years
- FX_return = exchange-rate return

The actual sign and calculation depend on the pair quotation
and position direction.

Use actual broker rollover data where available.
Do not assume that the policy-rate difference equals
the broker's financing charge.

## 5.3 Currency exposure

For a currency portfolio:

    CurrencyExposure_c = sum(position_value_i * exposure_i,c)

This must be calculated across all instruments.

For example, multiple positions may create hidden USD exposure
even when their symbols are different.

## 5.4 Macro-event risk

Represent important events using:

- Event timestamp.
- Currency affected.
- Event category.
- Previous value.
- Forecast value.
- Actual value.
- Publication timestamp.
- Source.
- Event importance.

Only values published by the simulated decision time may be used.

## 5.5 Acceptance criteria

- Correct pair conventions.
- Correct rollover calculation.
- Broker spread and financing included.
- Session calendars validated.
- Macro-event timestamps preserved.
- Currency exposure reconciles.
- Data outages fail closed.

---

# 6. PHASE 8 — OPTIONS

## 6.1 Objectives

Build options data and quantitative analytics.

Required fields:

- Underlying instrument.
- Expiry.
- Strike.
- Option type.
- Bid.
- Ask.
- Last trade.
- Volume.
- Open interest.
- Implied volatility.
- Contract multiplier.
- Settlement type.
- Exercise style.

## 6.2 Black-Scholes baseline

European call:

    C = S*N(d1) - K*exp(-rT)*N(d2)

European put:

    P = K*exp(-rT)*N(-d2) - S*N(-d1)

Where:

    d1 = [ln(S/K) + (r + sigma^2/2)*T] / (sigma*sqrt(T))

    d2 = d1 - sigma*sqrt(T)

Variables:

- S = underlying price
- K = strike
- r = continuously compounded risk-free rate
- T = time to expiry in years
- sigma = annualised volatility
- N = standard normal cumulative distribution function

Use the model as a baseline, not as a universal pricing truth.

## 6.3 Greeks

Delta:

    Delta = dV/dS

Gamma:

    Gamma = d2V/dS2

Vega:

    Vega = dV/dsigma

Theta:

    Theta = dV/dt

Rho:

    Rho = dV/dr

Greeks must be tested numerically against finite differences.

The implementation must document whether Vega is expressed
per 1.0 volatility or per one percentage point.

## 6.4 Heston model

Reference:

Empirical Performance of Stochastic Volatility Option Pricing Models.

ResearchGate:
https://www.researchgate.net/publication/343954660_Empirical_performance_of_stochastic_volatility_option_pricing_models

The Heston model:

    dS_t = mu*S_t*dt + sqrt(v_t)*S_t*dW_t

    dv_t = kappa*(theta-v_t)*dt
           + xi*sqrt(v_t)*dZ_t

    Corr(dW_t,dZ_t) = rho*dt

Where:

- v = instantaneous variance
- kappa = mean-reversion speed
- theta = long-run variance
- xi = volatility of variance
- rho = correlation

Compare models using:

- Pricing error.
- Hedging error.
- Calibration stability.
- Computational cost.
- Performance across volatility regimes.

## 6.5 Expiry and assignment

The system must model:

- Exercise style.
- Expiration date and time.
- Exercise cutoff.
- Assignment.
- Settlement.
- Early exercise where applicable.
- Corporate-action adjustments.
- Contract multiplier.

A pricing model alone does not constitute a complete options integration.

## 6.6 Acceptance criteria

- Contract metadata validated.
- Pricing models tested.
- Greeks independently verified.
- Implied volatility inversion tested.
- Expiry and settlement rules documented.
- Missing market data blocks dependent decisions.
- Assignment scenarios tested.

---

# 7. PHASE 9 — BETTING AND PREDICTION MARKETS

## 7.1 Objectives

Build probability estimation and market-price comparison systems.

The system must distinguish:

- Event probability.
- Market-implied probability.
- Model probability.
- Calibrated probability.
- Expected value.
- Available liquidity.
- Settlement outcome.

## 7.2 Brier score

    BS = (1/N) * sum((p_i-y_i)^2)

Where:

- p_i = predicted probability
- y_i = observed outcome (0 or 1)

Lower is better.

## 7.3 Log loss

    LL = -(1/N) * sum(
        y_i*ln(p_i) + (1-y_i)*ln(1-p_i)
    )

Probabilities must be clipped away from zero and one
for numerical stability.

## 7.4 Calibration

A model is calibrated when events assigned probability p
occur approximately p proportion of the time.

Use:

- Reliability diagrams.
- Expected calibration error.
- Brier score.
- Log loss.
- Calibration by event category.
- Calibration by probability bucket.
- Calibration over time.

Classification accuracy alone is insufficient.

Reference:

"Machine Learning for Sports Betting: Should Forecasting Models
Be Optimised for Accuracy or Calibration?"

ResearchGate:
https://www.researchgate.net/publication/369184023_Machine_learning_for_sports_betting_should_forecasting_models_be_optimised_for_accuracy_or_calibration

## 7.5 Kelly criterion

For decimal odds o:

    b = o - 1

    f* = (b*p - (1-p)) / b

Where:

- p = estimated probability of winning
- b = net odds
- f* = theoretical bankroll fraction

Fractional Kelly:

    f_actual = lambda * f*

Where 0 < lambda < 1.

A negative Kelly fraction must not become a positive wager.

Theoretical Kelly sizing assumes a sufficiently accurate probability
estimate and favourable available odds.

AXEL must reduce or reject sizing when probability uncertainty,
correlation, liquidity or settlement risk is material.

## 7.6 Market-implied probability

For decimal odds:

    p_raw = 1 / o

For a market with multiple outcomes, remove bookmaker margin
using a documented method.

Simple normalisation:

    p_i = (1/o_i) / sum_j(1/o_j)

This is one approximation, not a universal margin-removal method.

## 7.7 Settlement

Every event needs:

- Market identifier.
- Outcome definitions.
- Settlement source.
- Settlement timestamp.
- Void conditions.
- Cancellation handling.
- Dispute handling.
- Final result.
- Settlement status.

Do not evaluate a strategy using unverified or prematurely
settled outcomes.

## 7.8 Acceptance criteria

- Probabilities calibrated.
- Odds timestamps preserved.
- Market margin documented.
- Settlement logic tested.
- Void events handled.
- Expected-value calculations independently tested.
- Exposure limits enforced by Developer 2's deterministic core.

---

# 8. PHASE 10 — QUANTITATIVE RESEARCH AGENT SUPPORT

## 8.1 Research lifecycle

    Hypothesis
        |
        v
    Dataset registration
        |
        v
    Feature definition
        |
        v
    Experiment configuration
        |
        v
    Backtest
        |
        v
    Statistical validation
        |
        v
    Robustness testing
        |
        v
    Research report
        |
        v
    Human review

## 8.2 Reproducibility

Every experiment must contain:

- experiment_id
- hypothesis_id
- dataset_id
- dataset_hash
- feature_version
- model_version
- parameter_hash
- code_commit
- random_seed
- training_period
- validation_period
- holdout_period
- cost_model_version
- metrics
- execution_environment

A research result without reproducible inputs
must not qualify for promotion.

## 8.3 Multiple-testing control

Record every experiment, including unsuccessful experiments.

Do not report only the best model.

Recommended controls:

- Deflated Sharpe Ratio.
- Probability of Backtest Overfitting.
- False discovery rate procedures where appropriate.
- Untouched holdout.
- Walk-forward validation.
- Robustness perturbation.

## 8.4 Parameter perturbation

For parameter theta:

    theta_test = theta * (1 + delta)

Test a documented range of delta values.

For example:

    delta in {-0.20, -0.10, 0, 0.10, 0.20}

A strategy that only works at one exact parameter setting
should receive a robustness warning.

---

# 9. PHASE 11 — QUANTITATIVE PORTFOLIO VERIFICATION

Developer 1 supplies independent quantitative verification
for the portfolio intelligence system.

## 9.1 Portfolio variance

    sigma_p^2 = w' * Sigma * w

Where:

- w = portfolio weights
- Sigma = covariance matrix

## 9.2 Value at Risk

Under a normal-return approximation:

    VaR_alpha = -(mu_p + z_alpha*sigma_p)

This assumption must be explicit.

For non-normal returns, consider historical simulation,
filtered historical simulation or suitable tail models.

## 9.3 Expected Shortfall

    ES_alpha = E[L | L >= VaR_alpha]

Use consistent loss and confidence-level conventions.

## 9.4 Marginal risk contribution

    MRC_i = (Sigma*w)_i / sigma_p

Component contribution:

    RC_i = w_i * (Sigma*w)_i / sigma_p

Reconcile:

    sum(RC_i) = sigma_p

subject to the model's assumptions and numerical tolerance.

## 9.5 Quantitative verification checklist

- Covariance matrix is symmetric.
- Covariance matrix is positive semidefinite or handled explicitly.
- Missing returns are not silently replaced.
- Instruments are aligned by timestamp.
- Currency conversions are correct.
- Risk contributions reconcile.
- Stale prices are rejected.
- Transaction costs are included.
- Concentration limits are checked.
- Stress scenarios are reproducible.

---

# 10. SHARED DATA CONTRACTS

Developer 1 owns the producer side of:

1. Instrument metadata.
2. Market-data events.
3. Historical datasets.
4. Feature records.
5. Research experiment records.
6. Quantitative predictions.
7. Asset-specific metrics.

Developer 2 owns the consumer-side production integration
and portfolio/execution contracts.

All shared schemas must be versioned.

Breaking schema changes require coordinated migration.

Suggested common event envelope:

    event_id
    event_type
    schema_version
    source
    instrument_id
    event_timestamp
    published_timestamp
    received_timestamp
    payload
    payload_hash

All event processing should be idempotent.

---

# 11. TESTING STRATEGY

## 11.1 Unit tests

Test:

- Return calculations.
- Funding calculations.
- Greeks.
- Implied volatility.
- Kelly sizing.
- Probability calibration.
- Covariance calculations.
- Risk contributions.
- Data normalisation.

## 11.2 Property-based tests

Test invariants, not just examples.

Examples:

- Portfolio weights sum to the intended total.
- Probabilities remain within [0,1].
- A zero position has zero exposure.
- Missing data cannot create a valid signal.
- Risk contributions reconcile.
- A duplicate event does not duplicate a trade.
- Future observations never enter historical features.

## 11.3 Integration tests

Test provider failures, timestamp mismatches, missing fields,
rate limiting, data replay and schema migrations.

## 11.4 Backtest tests

Compare results against independently calculated examples.

Use deterministic fixtures.

Ensure the same dataset and configuration reproduce the same result.

---

# 12. DEVELOPER 1 DELIVERABLES

The developer must deliver:

1. Provider abstraction.
2. Canonical instrument model.
3. Historical ingestion.
4. Real-time ingestion.
5. Data validation.
6. Point-in-time data support.
7. Feature engineering framework.
8. Backtesting and cost model.
9. Walk-forward validation.
10. Crypto integration.
11. Forex integration.
12. Options integration.
13. Betting/prediction-market integration.
14. Research experiment reproducibility.
15. Quantitative portfolio verification.
16. Unit, integration and property tests.
17. Technical documentation.

---

# 13. EXIT CRITERIA

A phase is not complete simply because code exists.

It is complete only when:

- Required interfaces are implemented.
- Tests pass.
- Data lineage is preserved.
- Point-in-time integrity is demonstrated.
- Costs are modelled.
- Validation is reproducible.
- Failure conditions are documented.
- Integration contracts are accepted.
- Independent review is complete.

For any live asset class, the existence of a working adapter
does not constitute permission to trade.

---

# 14. RESEARCH REFERENCES

The following are research starting points. Verify the final
bibliographic metadata, publication status and full text before
treating any paper as an implementation authority.

## Backtesting and quantitative research

- Bailey et al. (2017), The Probability of Backtest Overfitting.
  https://doi.org/10.21314/JCF.2016.322

- Bailey and López de Prado, The Deflated Sharpe Ratio.
  https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf

- López de Prado, Advances in Financial Machine Learning.
  https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086

## Cryptocurrency

- Silantyev (2019), Order Flow Analysis of Cryptocurrency Markets.
  https://www.researchgate.net/publication/335736211_Order_Flow_Analysis_of_Cryptocurrency_Markets

## Forex

- Lustig, Roussanov and Verdelhan, Common Risk Factors in Currency Markets.
  https://doi.org/10.1111/j.1540-6261.2011.01675.x

## Options

- Heston (1993), A Closed-Form Solution for Options with Stochastic Volatility.
  https://doi.org/10.1093/rfs/6.2.327

- Black and Scholes (1973), The Pricing of Options and Corporate Liabilities.
  https://doi.org/10.1086/260062

## Betting

- Kelly (1956), A New Interpretation of Information Rate.
  https://doi.org/10.1002/j.1538-7305.1956.tb03809.x

- Brier (1950), Verification of Forecasts Expressed in Terms of Probability.
  https://doi.org/10.1175/1520-0493(1950)078%3C0001:VOFEIT%3E2.0.CO;2

- ResearchGate sports-betting calibration research:
  https://www.researchgate.net/publication/369184023_Machine_learning_for_sports_betting_should_forecasting_models_be_optimised_for_accuracy_or_calibration

## Implementation note

Research papers provide methods and evidence, not guaranteed
profitability. Every method must be tested using AXEL's own
data, costs, market conditions and operational constraints.
