# AXEL Quant Research Pack --- Risk, Dividend & Delisting Models

**Purpose:** Research inputs for the AXEL quantitative trading/risk
system.

**Important:** This document is an implementation-oriented research
digest. It does **not** reproduce copyrighted papers. Use the source
links to obtain the full papers where access is permitted.

------------------------------------------------------------------------

## 0. Research policy

Do not treat a reported accuracy/metric in a paper as a production
guarantee.

For AXEL, every research result should be re-tested with:

-   point-in-time data
-   no look-ahead leakage
-   walk-forward validation
-   realistic transaction costs
-   slippage
-   delisted securities retained in the historical universe
-   class-imbalance-aware metrics
-   out-of-sample testing
-   regime/stress testing
-   feature availability timestamps

------------------------------------------------------------------------

# 1. Risk per trade / hedge-fund risk management

## 1.1 Philippe Jorion --- Risk Management for Hedge Funds with Position Information

**Year:** 2006/2007\
**DOI:** 10.3905/jpm.2007.698042\
**ResearchGate:**\
https://www.researchgate.net/publication/411431571_Risk_Management_for_Hedge_Funds_with_Position_Information

### Core finding

Traditional risk measurement based only on historical returns can be
unreliable for dynamic trading strategies. Jorion describes using
current position information to calculate Value at Risk (VaR), with
frequent measurement for rapidly changing portfolios.

A key implementation concept is **ex-ante risk control using current
positions**.

### AXEL implementation implications

Do not make the risk engine simply:

``` text
risk_per_trade = fixed_percentage
```

Instead:

``` text
signal
  -> proposed position
  -> current portfolio exposure
  -> volatility
  -> correlations
  -> nonlinear/option exposure
  -> VaR / tail risk
  -> risk budget
  -> maximum permitted position
```

### Candidate AXEL controls

``` yaml
risk:
  max_portfolio_var: configurable
  max_expected_shortfall: configurable
  max_strategy_exposure: configurable
  max_asset_exposure: configurable
  max_sector_exposure: configurable
  max_leverage: configurable
  max_drawdown: configurable
  stress_loss_limit: configurable
  position_recalculation_frequency: configurable
```

### Important interpretation

This paper supports **dynamic risk measurement**, not a universal fixed
percentage such as 1% or 2% risk per trade.

------------------------------------------------------------------------

## 1.2 Cassar & Gerakos --- Do Risk Management Practices Work? Evidence from Hedge Funds

**Year:** 2017\
**Journal:** Review of Accounting Studies\
**DOI:** 10.1007/s11142-017-9403-5\
**ResearchGate:**\
https://www.researchgate.net/publication/317212772_Do_risk_management_practices_work_Evidence_from_hedge_funds

### Empirical result

The study examines hedge-fund risk-management practices around the 2008
financial crisis.

The authors report that funds using **formal risk models** performed
significantly better during extreme down months of 2008. They did not
find evidence that merely having position limits or a dedicated head of
risk management was associated with reduced left-tail risk.

Funds using VaR models also had more accurate expectations of
performance in a short-term equity bear market.

### AXEL implementation implications

A risk system should evaluate whether a proposed position changes the
**portfolio's total tail risk**, not just whether the individual trade
is below a fixed limit.

Recommended pipeline:

``` text
Trade proposal
    |
    +--> standalone trade risk
    |
    +--> marginal portfolio VaR
    |
    +--> marginal Expected Shortfall
    |
    +--> concentration
    |
    +--> correlation
    |
    +--> stress scenarios
    |
    +--> liquidity impact
    |
    v
Risk decision
```

------------------------------------------------------------------------

## 1.3 StressVaR --- Coste, Douady & Zovko

**ResearchGate:**\
https://www.researchgate.net/publication/228294278_The_StressVaR_A_New_Risk_Concept_for_Extreme_Risk_and_Fund_Allocation

### Research relevance

StressVaR is aimed at extreme-risk measurement and fund allocation. It
is useful as research for AXEL's **tail-risk layer**.

### AXEL concept

Maintain separate:

``` text
Normal Risk
  - volatility
  - VaR
  - Expected Shortfall

Extreme Risk
  - historical stress
  - scenario shocks
  - tail dependence
  - StressVaR-style measures
```

------------------------------------------------------------------------

## 1.4 Syriopoulos --- Hedge Funds and Risk Management

**ResearchGate:**\
https://www.researchgate.net/publication/286140196_Hedge_Funds_and_Risk_Management

### Topics relevant to AXEL

-   VaR
-   Expected Shortfall
-   Extreme Value Theory
-   Generalized Pareto distributions
-   Monte Carlo simulation
-   Historical simulation
-   stress testing
-   scenario analysis
-   copulas

### Suggested AXEL use

Treat this as a **risk-methodology reference**, rather than a single
predictive model.

------------------------------------------------------------------------

# 2. Dividend prediction

## 2.1 Ivașcu --- Understanding Dividend Puzzle Using Machine Learning

**Year:** 2023/2024\
**Journal:** Computational Economics\
**DOI:** 10.1007/s10614-023-10439-7\
**ResearchGate:**\
https://www.researchgate.net/publication/372988706_Understanding_Dividend_Puzzle_Using_Machine_Learning

### Methods

The study uses:

-   Principal Component Analysis
-   multiple ML models
-   resampling techniques
-   multiple evaluation metrics
-   SHAP/model interpretation

### Reported finding

Company size is reported as the most informative determinant of
dividend-paying propensity in the study, with larger and less risky
firms more likely to pay dividends.

### AXEL target design

Do not reduce the dividend model to a single binary target.

Consider:

``` text
P(dividend_increase)
P(dividend_unchanged)
P(dividend_decrease)
P(dividend_omission)
expected_dividend_growth
expected_dividend_yield
```

### Feature families

``` text
Firm characteristics
  - market capitalization
  - total assets
  - revenue
  - profitability
  - leverage
  - liquidity
  - risk

Dividend history
  - previous dividend
  - payout ratio
  - dividend growth
  - omission history

Market variables
  - volatility
  - returns
  - valuation

Macro variables
  - rates
  - inflation
  - economic growth
```

------------------------------------------------------------------------

## 2.2 Bhat --- Predicting Dividend Omission Behaviour of Indian Firms Using Machine Learning Algorithms

**Year:** 2022\
**ResearchGate:**\
https://www.researchgate.net/publication/339324945_Predicting_Dividend_Omission_Behaviour_of_Indian_Firms_Using_Machine_Learning_Algorithms

### Dataset

The paper reports:

-   12,942 firm-year observations
-   Indian manufacturing and non-financial services firms
-   2013--2018
-   55% dividend omissions

### Models tested

-   Logistic Regression
-   Naive Bayes
-   Decision Tree
-   Random Forest
-   Gradient Boosting Trees
-   Support Vector Machines
-   Artificial Neural Networks
-   MLP / RProp

### Reported performance

The MLP ANN achieved approximately:

``` text
Accuracy: 82.36%
ROC AUC: 0.901
```

Gradient Boosted Trees:

``` text
Accuracy: 82.31%
```

Random Forest:

``` text
Accuracy: 80.72%
```

### Reported informative feature groups

-   profitability
-   size
-   efficiency
-   financial risk
-   growth
-   liquidity

The paper explicitly cautions that predictive variable importance does
not establish causal direction.

### AXEL implementation

Create a dedicated:

``` text
DividendOmissionModel
```

with:

``` text
input:
  financial_features
  market_features
  dividend_history
  firm_lifecycle_features

output:
  probability_of_omission
  confidence
  feature_importance
```

------------------------------------------------------------------------

# 3. Delisting prediction

## 3.1 Neuhäusler, Yoon, Levine & Fan --- Prediction of Delisting Using a Machine Learning Ensemble

**Year:** 2025\
**Journal:** International Journal of Statistics and Probability\
**DOI:** 10.5539/ijsp.v14n3p58

**ResearchGate:**\
https://www.researchgate.net/publication/395111014_Prediction_of_Delisting_Using_a_Machine_Learning_Ensemble

**Open publisher PDF:**\
https://ccsenet.org/journal/index.php/ijsp/article/download/0/0/52132/56762

**Author code repository:**\
https://github.com/david01neu/delistingml

### Dataset

The paper uses:

``` text
8,870 companies
1970–2022
NYSE
NYSE American
NASDAQ
quarterly financial data
macro-economic variables
```

The study reports a training/test split of:

``` text
6,652 listed companies
2,218 test companies
```

### Target

Predict whether delisting occurs within approximately the following
one-year prediction interval.

### Feature engineering

The paper uses historical financial-ratio information and constructs
changes/trends using previous quarterly reports.

A particularly useful design:

``` text
historical ratio series
       |
       v
trend / slope calculation
       |
       v
current financial state + historical deterioration
       |
       v
delisting probability
```

The paper describes using up to 20 previous quarterly report dates for
slope-based feature construction, with a one-quarter gap before the
prediction interval.

### Models

Five base learners:

``` text
1. Logistic Regression
2. Random Forest
3. Gradient Boosting
4. Support Vector Machine
5. Neural Network
```

These are combined using an ensemble/meta-learning approach.

### Validation

The paper uses:

``` text
10-fold cross-validation
hyperparameter grid search
ROC-based thresholding
MCC/F1-based thresholding
held-out test evaluation
```

### Reported test result

For the MCC/F1 threshold variant, the paper reports approximately:

``` text
Accuracy: 0.8363
MCC:      0.4566
```

### Reported informative predictors

Among the most informative variables:

``` text
Price/Earnings ratio
Return on Equity
Operating Profit Margin
Cash Ratio
Consumer Price Index / inflation
```

### AXEL implementation

Recommended architecture:

``` text
                    Company
                       |
        +--------------+--------------+
        |              |              |
    Financial       Market          Macro
    statements      data            data
        |              |              |
        +--------------+--------------+
                       |
                 Feature engine
                       |
       +---------------+----------------+
       |               |                |
      LR              RF              GBM
       |               |                |
       +---------------+----------------+
                       |
                      SVM
                       |
                       NN
                       |
                 Meta learner
                       |
                       v
              P(delisting <= 1Y)
```

------------------------------------------------------------------------

## 3.2 Endri, Kasmir & Dewi --- Delisting Sharia Stock Prediction Model Based on Financial Information: Support Vector Machine

**Year:** 2020\
**DOI:** 10.5267/j.dsl.2019.11.001\
**ResearchGate:**\
https://www.researchgate.net/publication/338830139_Delisting_sharia_stock_prediction_model_based_on_financial_information_Support_Vector_Machine

**Open publisher PDF:**\
https://www.growingscience.com/dsl/Vol9/dsl_2019_28.pdf

### Dataset

``` text
335 sharia stocks in population
102 companies in sample
2012–2017
```

### Features

``` text
Debt / Equity
Return on Invested Capital
Asset Turnover
Quick Ratio
Current Ratio
Return on Assets
Return on Equity
Leverage
Long-Term Debt
Interest Coverage
```

### Model

Support Vector Machine with multiple model configurations.

The paper reports different test accuracies across four SVM
configurations.

### Important caution

One configuration reports 100% accuracy.

**Do not implement this as evidence that AXEL can achieve 100%
real-world delisting prediction.**

The sample is small and the result requires careful validation for
overfitting, selection effects and generalization.

Use this paper mainly for:

``` text
feature engineering ideas
SVM configuration ideas
financial-distress feature families
```

------------------------------------------------------------------------

## 3.3 Thompson & Kim --- On Modeling Acquirer Delisting Post-Merger Using Machine Learning Techniques

**Year:** 2024\
**DOI:** 10.1080/23270012.2024.2348475\
**ResearchGate:**\
https://www.researchgate.net/publication/380741804_On_modeling_acquirer_delisting_post-merger_using_machine_learning_techniques

### Methodological relevance

The paper uses a tuned Random Forest model and SHAP-based interpretation
for a specific post-merger delisting problem.

### AXEL use

This is useful for building:

``` text
DelistingModel
+
SHAP explanation
```

so that AXEL can output:

``` json
{
  "probability": 0.71,
  "top_risk_factors": [
    "profitability deterioration",
    "leverage",
    "post_merger characteristics"
  ]
}
```

Do not treat this as a general-market delisting model; it addresses a
specific post-merger population.

------------------------------------------------------------------------

# 4. Recommended AXEL model interfaces

## 4.1 Risk Engine

``` python
class RiskEngine:
    def calculate_var(self, portfolio, horizon, confidence):
        ...

    def calculate_expected_shortfall(self, portfolio, horizon, confidence):
        ...

    def calculate_stress_loss(self, portfolio, scenario):
        ...

    def calculate_marginal_risk(self, portfolio, proposed_trade):
        ...

    def calculate_position_limit(self, signal, portfolio, market_state):
        ...
```

------------------------------------------------------------------------

## 4.2 Dividend Model

``` python
class DividendModel:
    def predict_payment(self, features):
        ...

    def predict_increase(self, features):
        ...

    def predict_decrease(self, features):
        ...

    def predict_omission(self, features):
        ...

    def explain(self, features):
        ...
```

------------------------------------------------------------------------

## 4.3 Delisting Model

``` python
class DelistingModel:
    def predict_probability(self, features):
        ...

    def predict_horizon(self, features):
        ...

    def explain(self, features):
        ...

    def stress_test(self, features):
        ...
```

------------------------------------------------------------------------

# 5. Research-derived factor registry

This should become a machine-readable factor registry inside AXEL.

## Risk factors

``` text
portfolio_volatility
asset_volatility
correlation
portfolio_var
expected_shortfall
stress_loss
leverage
concentration
liquidity
tail_exposure
option_nonlinearity
```

## Dividend factors

``` text
firm_size
profitability
efficiency
liquidity
financial_risk
growth
payout_ratio
dividend_history
dividend_growth
earnings
cash_flow
leverage
```

## Delisting factors

``` text
pe_ratio
roe
operating_profit_margin
cash_ratio
inflation
debt_equity
roic
asset_turnover
quick_ratio
current_ratio
roa
leverage
long_term_debt
interest_coverage
financial_ratio_trends
market_performance
gdp_growth
interest_rate
```

------------------------------------------------------------------------

# 6. What AXEL should NOT copy blindly

## Fixed risk percentage

Research does not justify one universal:

``` text
1% per trade
```

or:

``` text
2% per trade
```

Instead, risk should depend on:

``` text
signal quality
+
volatility
+
liquidity
+
portfolio exposure
+
correlation
+
tail risk
+
drawdown
+
strategy risk budget
```

## Paper-reported accuracy

A paper's accuracy is not a production expectation.

For financial classification, AXEL should prioritize:

``` text
MCC
ROC-AUC
PR-AUC
precision
recall
calibration
Brier score
expected loss
economic value
```

and ultimately evaluate whether the prediction improves the
**trading/risk decision after costs**.

------------------------------------------------------------------------

# 7. Priority order for implementation

## Phase R1 --- Risk

Read:

1.  Jorion
2.  Cassar & Gerakos
3.  StressVaR
4.  Syriopoulos

Implement:

``` text
VaR
Expected Shortfall
marginal risk
stress testing
position limits
portfolio risk budget
```

## Phase R2 --- Dividend

Read:

1.  Ivașcu
2.  Bhat

Implement:

``` text
dividend payment model
dividend change model
dividend omission model
SHAP/explainability
```

## Phase R3 --- Delisting

Read:

1.  Neuhäusler et al. 2025
2.  Endri et al. 2020
3.  Thompson & Kim 2024

Implement:

``` text
financial-ratio feature engine
ratio trend engine
delisting classifier
ensemble
probability calibration
SHAP explanations
```

------------------------------------------------------------------------

# 8. Most important paper for immediate AXEL implementation

For the **delisting module**, start with:

**Prediction of Delisting Using a Machine Learning Ensemble (2025)**

It is especially useful because it provides a relatively large U.S.
historical sample, explicit feature engineering, multiple ML models,
ensemble methodology, cross-validation, class-imbalance-aware MCC
evaluation, and an author-maintained code repository.

For the **dividend module**, start with:

**Predicting Dividend Omission Behaviour of Indian Firms Using Machine
Learning Algorithms (2022)**

For the **risk module**, start with:

**Risk Management for Hedge Funds with Position Information (Jorion)**

and then validate the institutional-risk design against:

**Do Risk Management Practices Work? Evidence from Hedge Funds**

------------------------------------------------------------------------

# 9. Source index

  ------------------------------------------------------------------------------
  ID                Paper                Main AXEL use         Full text status
  ----------------- -------------------- --------------------- -----------------
  R1                Jorion --- Risk      Dynamic VaR /         ResearchGate
                    Management for Hedge position risk         page; full text
                    Funds with Position                        may require
                    Information                                author/request

  R2                Cassar & Gerakos --- Empirical hedge-fund  ResearchGate
                    Do Risk Management   risk management       
                    Practices Work?                            

  R3                Coste/Douady/Zovko   Extreme/tail risk     ResearchGate
                    --- StressVaR                              

  R4                Syriopoulos ---      Risk methodology      ResearchGate
                    Hedge Funds and Risk                       
                    Management                                 

  D1                Ivașcu ---           Dividend propensity   ResearchGate
                    Understanding                              
                    Dividend Puzzle                            
                    Using ML                                   

  D2                Bhat --- Predicting  Dividend omission ML  ResearchGate
                    Dividend Omission                          full-text PDF
                    Behaviour                                  

  X1                Neuhäusler et        U.S. delisting ML     ResearchGate +
                    al. --- Prediction                         open publisher
                    of Delisting Using                         PDF
                    ML Ensemble                                

  X2                Endri et al. ---     Financial-ratio/SVM   ResearchGate +
                    Delisting Sharia     delisting             open publisher
                    Stock Prediction                           PDF

  X3                Thompson & Kim ---   RF + SHAP             ResearchGate;
                    Acquirer Delisting                         full text may
                    Post-Merger                                require request
  ------------------------------------------------------------------------------

------------------------------------------------------------------------

# 10. AXEL research rule

Every factor imported from research should receive:

``` text
factor_id
source_paper
source_year
source_market
source_period
source_population
feature_definition
target_definition
prediction_horizon
validation_method
reported_metric
known_limitations
point_in_time_requirement
AXEL_retest_status
```

Example:

``` yaml
factor_id: delisting_pe_ratio
source: "Neuhäusler et al. 2025"
source_market: "US"
source_period: "1970-2022"
feature_type: "fundamental"
target: "delisting_within_one_year"
importance: "high_in_source_study"
validation: "10-fold CV + held-out test"
axel_status: "UNVALIDATED"
```

**Never mark a factor `PROVEN` in AXEL merely because a paper found it
statistically/predictively useful. It becomes an AXEL-validated factor
only after independent point-in-time out-of-sample testing.**
