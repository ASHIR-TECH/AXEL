# AXEL Phase 6 --- Research Dissection and OpenCode Development Specification

**Document type:** Research-to-implementation engineering specification\
**Project:** AXEL v2\
**Phase:** 6 --- Tiny Live Stocks\
**Prepared:** 2026-10-12\
**Purpose:** Convert the available execution-cost, order-execution, and
risk-management research into a modular development plan that OpenCode
can implement against the existing AXEL Phase 6 PRD.

> **Safety invariant:** LLMs and agents may propose trades; only
> deterministic Trading Core code may validate and authorize them. The
> independent Watchdog must be able to halt new risk-taking. Research
> findings never override Phase 5 promotion requirements, live
> configuration gates, broker/account permissions, or risk limits.

------------------------------------------------------------------------

## 0. Scope, source coverage, and evidence labels

This document combines the existing AXEL Phase 6 PRD with the research
sources listed below. It is not a claim that every ResearchGate entry is
peer reviewed or that every source provides a full paper.

### Source coverage

  -------------------------------------------------------------------------------
  ID                Source            Availability/status   Main relevance
  ----------------- ----------------- --------------------- ---------------------
  R1                Vladimir Markov,  Full preprint text    Transaction-cost
                    **Bayesian        available via         analysis (TCA),
                    Trading Cost      ResearchGate/arXiv;   benchmark choice,
                    Analysis and      preprint status       robust cost
                    Ranking of Broker should be retained in distributions,
                    Algorithms**      citations             partial pooling
                    (2019)                                  

  R2                Miles Kumaresan,  Open-access journal   Retrospective
                    Nataša Krejić,    article; full text    execution-quality
                    Sanja Lončar,     available             metric, market
                    **Negative                              conditions, order
                    selection---a new                       placement and fill
                    performance                             behavior
                    measure for                             
                    automated order                         
                    execution**                             
                    (2021)                                  

  R3                Romain Loras,     ResearchGate          Fees, spread,
                    **The impact of   full-text listing and slippage, realistic
                    transactions      abstract were         backtests
                    costs and         available; this       
                    slippage on       specification relies  
                    algorithmic       on the accessible     
                    trading           abstract and metadata 
                    performance**     rather than asserting 
                    (2024)            a complete            
                                      independent           
                                      verification of every 
                                      section               

  R4                Riccardo Cesari,  Full working-paper    Order types, limit
                    Massimiliano      text available        order book, execution
                    Marzo, Paolo                            tactics, transaction
                    Zagaglia,                               costs, optimal
                    **Effective Trade                       execution
                    Execution**                             
                    (2012; also                             
                    published as a                          
                    book chapter)                           

  R5                Ernest P. Chan,   Book chapter /        Optimal leverage,
                    **Risk            publisher contents    constant-proportion
                    Management**, in  page; not treated as  portfolio insurance,
                    *Algorithmic      a standalone open     stop-losses, risk
                    Trading: Winning  research paper        indicators
                    Strategies and                          
                    Their Rationale*                        
                    (2013)                                  

  R6                Zoltán Eisler and arXiv/ResearchGate    Separate short-term
                    Johannes          preprint; full        execution quality
                    Muhle-Karbe,      preprint available,   from price impact;
                    **Optimizing      but it is a preprint  intraday cost
                    Broker            rather than a         estimation
                    Performance       peer-reviewed version 
                    Evaluation        in the sources        
                    through Intraday  reviewed              
                    Modeling of                             
                    Execution Cost**                        
                    (2024)                                  

  P6                **AXEL v2 ---     Existing project      Authoritative scope,
                    Phase 6 Product   document              trust boundary, live
                    Requirements                            gate, risk controls,
                    Document**                              order FSM,
                                                            reconciliation,
                                                            incidents, rollout
  -------------------------------------------------------------------------------

### Evidence labels used throughout

-   **\[SOURCE\]** --- directly supported by the source's stated
    abstract, paper text, or existing AXEL Phase 6 PRD.
-   **\[ENGINEERING APPLICATION\]** --- a concrete AXEL design
    recommendation derived from source concepts; not claimed to be a
    verbatim requirement from the paper.
-   **\[OPEN QUESTION\]** --- a choice that must be resolved against the
    repository, selected broker API, deployment, or reviewed risk
    configuration.
-   **\[MUST NOT\]** --- a safety boundary that must not be weakened by
    implementation convenience.

### Primary links

-   R1 --- ResearchGate:
    <https://www.researchgate.net/publication/332169136_Bayesian_Trading_Cost_Analysis_and_Ranking_of_Broker_Algorithms>\
    arXiv: <https://arxiv.org/abs/1904.01566>
-   R2 --- ResearchGate:
    <https://www.researchgate.net/publication/350334242_Negative_selection-a-new-performance-measure-for-automated-order-execution>\
    Publisher full text:
    <https://link.springer.com/article/10.1186/s13362-021-00102-x>\
    DOI: <https://doi.org/10.1186/s13362-021-00102-x>
-   R3 --- ResearchGate:
    <https://www.researchgate.net/publication/384458498_The_impact_of_transactions_costs_and_slippage_on_algorithmic_trading_performance>
-   R4 --- ResearchGate:
    <https://www.researchgate.net/publication/228059126_Effective_Trade_Execution>\
    arXiv: <https://arxiv.org/abs/1206.5324>\
    Working paper PDF: <https://rcea.org/RePEc/pdf/wp41_12.pdf>
-   R5 --- Publisher contents:
    <https://www.oreilly.com/library/view/algorithmic-trading-winning/9781118746912/OEBPS/9781118746912_epub_toc.htm>\
    This is a book chapter. Do not treat it as a freely accessible
    standalone paper.
-   R6 --- ResearchGate:
    <https://www.researchgate.net/publication/380974826_Optimizing_Broker_Performance_Evaluation_through_Intraday_Modeling_of_Execution_Cost>\
    arXiv: <https://arxiv.org/abs/2405.18936>\
    SSRN PDF landing:
    <https://papers.ssrn.com/sol3/Delivery.cfm/4846427.pdf?abstractid=4846427&mirid=1>

------------------------------------------------------------------------

# 1. Executive implementation decision

Phase 6 is **not a new alpha/strategy phase**. Its job is to prove that
the already-validated Stocks strategy can submit and manage real orders
inside a deliberately tiny, explicitly approved capital envelope.

The research points to five implementation priorities:

1.  **Fail-closed authorization:** no order reaches the broker unless
    the phase gate, live configuration, manual approval, risk checks,
    data freshness, and Watchdog health checks all pass.
2.  **Correct, durable order lifecycle:** model order states explicitly;
    handle timeouts, partial fills, cancellation races, restarts, and
    duplicate messages without blind resubmission.
3.  **Broker reconciliation as an independent source-of-truth check:**
    compare AXEL state with broker orders, fills, positions, cash, and
    account state; never silently overwrite a mismatch.
4.  **Per-order and per-fill transaction-cost measurement:** record the
    prices and timestamps needed to compute implementation shortfall,
    VWAP-relative slippage where meaningful, fees, spread cost where
    quote data exists, and post-trade diagnostics.
5.  **Evidence-based rollout:** use paper/sandbox and failure-injection
    tests first; begin tiny-live only after the Phase 5 promotion gate
    has passed and evidence is verified.

**\[SOURCE\]** R1 explains why execution costs can erase strategy
returns and why transaction-cost analysis needs robust benchmark
distributions and careful benchmark selection. R2 argues that
conventional benchmarks can misrepresent execution quality and
introduces a retrospective Negative Selection metric. R4 provides
order-type and market-microstructure background. R5 identifies leverage,
stop-losses, and risk indicators as risk-management topics. R6 models
short-term execution quality and price impact separately. P6 requires
live gating, approval, deterministic risk, broker integration,
reconciliation, incident handling, halt/recovery, and full auditability.

**\[ENGINEERING APPLICATION\]** AXEL should first implement reliable
event capture and safety behavior. Advanced execution ML or automatic
broker ranking is a later enhancement, not a prerequisite for the first
safe tiny-live deployment.

------------------------------------------------------------------------

# 2. Research dissection

## 2.1 R1 --- Bayesian Trading Cost Analysis and Ranking of Broker Algorithms

### Research question and contribution

**\[SOURCE\]** Markov frames transaction-cost analysis as a Bayesian
estimation problem, especially when comparing execution algorithms with
limited observations. The paper discusses standardized benchmarks
including implementation shortfall (IS), volume-weighted average price
(VWAP), participation-weighted price (PWP), and short-term reversion. It
emphasizes that benchmark observations can have fat tails, skewness, and
heteroscedasticity. It proposes hierarchical / partially pooled models
so that observations from a larger aggregate sample can inform estimates
for an algorithm with fewer observations.

The paper also distinguishes schedule-based execution algorithms---such
as TWAP, VWAP, implementation-shortfall, and percentage-of-volume---from
liquidity-seeking algorithms. Different tactics optimize different
objectives and constraints. It cautions against relying on a single
benchmark: a benchmark can be noisy or create incentives that do not
correspond to total execution cost.

### What AXEL should take from it

1.  **Record raw execution evidence before building an estimator.** A
    statistical model cannot repair missing arrival prices, timestamps,
    fees, quote data, partial fills, or order characteristics.
2.  **Keep multiple metrics.** Do not declare an execution method "best"
    from one VWAP or slippage statistic.
3.  **Treat small samples cautiously.** Early tiny-live data will be
    sparse. A ranking should include uncertainty, sample size, and
    market-condition context.
4.  **Use a simple baseline first.** Begin with transparent arithmetic
    metrics and descriptive summaries. Only add a Bayesian model once
    the system has sufficient trustworthy observations and the baseline
    is tested.
5.  **Stratify comparisons.** Cost depends on symbol liquidity, order
    notional/size, volatility, spread, time of day, order type, side,
    and participation relative to observed volume. Comparisons that
    ignore these differences can be misleading.
6.  **Do not equate benchmark achievement with best execution.** Meeting
    a VWAP target does not prove minimum total cost; aggressive
    participation can make one benchmark look good while worsening
    another.

### Benchmark definitions for implementation

All prices and costs must be normalized to a consistent side and sign
convention. Store raw values first; derive metrics in a tested service.

Let: - (Q) = total requested quantity; - (q_i) = quantity of fill (i); -
(p_i) = fill price (i); - (P_a) = arrival/decision reference price
recorded before submission; - (P_v) = VWAP benchmark over the defined
measurement window; - (s=+1) for a buy and (s=-1) for a sell.

Average fill price:

\[ P\_{`\mathrm{fill}`{=tex}}=`\frac{\sum_i q_i p_i}{\sum_i q_i}`{=tex}
\]

Signed implementation shortfall per share (positive means worse
execution):

\[ IS\_{`\mathrm{per\ share}`{=tex}}=s(P\_{`\mathrm{fill}`{=tex}}-P_a)
\]

Dollar implementation shortfall on filled quantity:

\[ IS\_{`\mathrm{filled}`{=tex}}=s`\sum`{=tex}\_i q_i(p_i-P_a) \]

If the strategy also has a meaningful unfilled quantity, track
opportunity cost separately rather than hiding it inside filled
slippage. A fully consistent implementation-shortfall calculation may
include explicit opportunity cost, commissions/fees, and market impact;
document the chosen definition and never change it silently.

VWAP over eligible market prints:

\[ P_v=`\frac{\sum_j v_j p_j}{\sum_j v_j}`{=tex} \]

VWAP-relative signed cost per share:

\[ C\_{`\mathrm{VWAP}`{=tex}}=s(P\_{`\mathrm{fill}`{=tex}}-P_v) \]

**Critical caveat:** the market-data window, venue coverage,
trade-condition filters, time zone, and handling of the strategy's own
prints must be specified. VWAP is a comparison benchmark, not a
universal best-execution oracle.

Short-term reversion: capture a configured post-completion reference
price or a sequence of reference prices after the final fill, then
compute a side-adjusted price movement. The exact time horizon(s) must
be fixed and versioned; it is a diagnostic, not proof of causal market
impact.

### Bayesian model: later-stage, not day-one

**\[ENGINEERING APPLICATION\]** If AXEL eventually estimates cost
distributions, the data row should include a benchmark observation plus
explanatory variables such as: - symbol / liquidity bucket; - side; -
order notional and quantity; - spread and volatility at arrival; - order
type and time-in-force; - order duration; - market session/time
bucket; - participation rate, if reliable volume data exists; -
strategy/playbook version; - broker/route/algorithm identifier if
exposed; - fill ratio and cancellation status.

Use a baseline robust regression / grouped summary first. A later
hierarchical model can partially pool sparse algorithm- or symbol-level
observations toward broader group estimates. Store predictive intervals
or uncertainty measures, not only a point estimate. Do not train or
deploy this model in the live risk authorization path in Phase 6.

### AXEL work packages

-   `P6-OBS`: collect all raw fields and compute transparent metrics.
-   `P6-BROKER`: expose broker and order-type metadata if available.
-   `P6-RECON`: ensure filled quantities and prices match broker
    records.
-   `P6-QA`: unit-test sign conventions, partial fills, zero fills,
    fees, and benchmark-window rules.

### Acceptance criteria derived for AXEL

-   Every fill can be linked to its order, proposal, approval, strategy
    version, and risk decision.
-   Metrics have a versioned definition and include benchmark
    source/window.
-   Reports display sample size and missing-data rate.
-   No execution algorithm is ranked from an insufficient sample without
    an "insufficient evidence" status.
-   A benchmark comparison cannot modify risk limits or bypass the live
    gate.

------------------------------------------------------------------------

## 2.2 R2 --- Negative Selection: A New Performance Measure for Automated Order Execution

### Research question and contribution

**\[SOURCE\]** Kumaresan, Krejić, and Lončar argue that VWAP and
arrival-price implementation shortfall have limitations. VWAP can be
affected by the trader's own activity; arrival-price IS references a
static price and may not fully represent what was achievable during the
entire execution window. The paper defines **Negative Selection (NS)**
as a retrospective measure based on an "optimal placement" that could
have been selected if the complete market activity during the window had
been known in advance.

The paper formulates optimal placement as a linear-programming problem
over price levels and available order-book quantities. NS compares the
hypothetical optimal placement with the actual placement using gain
coefficients. It reports theoretical properties and empirical tests on
historical tick data for four European equities. The authors explicitly
state that NS is a **post-trade measurement**, not a predictive model.

For a simple buy order, the paper defines price levels (P_j), with (P_0)
as the initial ask and (P_1,`\ldots`{=tex},P_k) as bid levels. Gain
coefficients are:

\[ g_j=`\frac{P_0-P_j}{P_0}`{=tex} \]

The paper's single-order definition is:

\[
`\mathcal{N}`{=tex}=(`\mathcal{O}`{=tex}-`\mathcal{Q}`{=tex})\^T`\mathcal{G}`{=tex}
\]

where (`\mathcal{O}`{=tex}) is the hypothetical optimal placement
vector, (`\mathcal{Q}`{=tex}) is the actual order vector, and
(`\mathcal{G}`{=tex}) is the gain-coefficient vector. Sign
interpretation follows the paper's convention; AXEL must not relabel
positive/negative values without documenting its implementation.

The paper describes desirable properties: an optimal placement has zero
NS; fully filled orders have nonnegative NS under the paper's
convention; unfilled orders have negative NS; and placement/size changes
have structured effects. These properties are specific to the paper's
mathematical setup and must be reproduced by tests before the metric is
trusted.

### What AXEL should take from it

1.  **Keep post-trade hindsight separate from live decisions.** NS
    requires the realized market window and must never be supplied as a
    feature available at order-decision time.
2.  **Benchmark execution against the market conditions that actually
    occurred.** A static arrival price alone cannot explain every
    execution outcome.
3.  **Treat passive/aggressive trade-offs explicitly.** A passive order
    may save spread if filled but can remain unfilled while price moves
    away; aggressive orders may complete but cross the spread and walk
    the book.
4.  **Respect fill ratio and order size.** A metric should not treat a
    partially filled order as identical to a completed order.
5.  **Avoid false precision.** The paper's empirical dataset is
    historical and its market structure is not automatically
    representative of AXEL's current broker, US-listed equities, or
    order sizes.

### Implementation decision

**Phase 6 minimum:** store enough data to calculate ordinary execution
metrics reliably.

**Optional Phase 6.1 / later research task:** implement NS as a
retrospective analysis only if AXEL has sufficiently granular
market-depth/queue data for the entire order window. Top-of-book bars
alone are not enough to claim faithful reproduction of the paper's
order-book optimization.

The NS implementation must: - live in an analytics/research module, not
in `core/risk`, `execution`, or `watchdog`; - be explicitly marked
`post_trade_only`; - use point-in-time order-book snapshots and trades,
with documented gaps; - identify when data coverage is insufficient and
return `NOT_COMPUTABLE`; - retain the calculation version and data
coverage metadata; - be compared against IS, VWAP, fill ratio, and
post-trade reversion; - never feed live authorization or alter position
sizing in Phase 6.

### Required tests if implemented

-   Optimal placement compared with itself produces zero under the
    paper's convention.
-   Fully filled and unfilled cases reproduce the expected sign
    properties.
-   Buy/sell symmetry is tested rather than assumed.
-   More passive placements produce expected changes under synthetic
    rising, falling, and sideways price paths.
-   Partial fills and multiple price levels are covered.
-   Missing depth snapshots produce a non-computable result, not a
    fabricated metric.
-   No online/live code path imports or invokes the retrospective NS
    module.

### AXEL work packages

-   `P6-OBS`: capture the data required for future evaluation.
-   `P6-QA`: prove there is no look-ahead leakage.
-   Later research ticket, not a Phase 6 launch blocker:
    `P6-NS-RESEARCH`.

------------------------------------------------------------------------

## 2.3 R3 --- The Impact of Transactions Costs and Slippage on Algorithmic Trading Performance

### Source coverage and limits

**\[SOURCE\]** The accessible ResearchGate abstract describes
transaction costs and slippage as factors that erode algorithmic-trading
returns. It identifies commissions/fees and bid-ask spreads as
transaction-cost components and slippage as the difference between
expected and actual execution prices caused by market dynamics. It
recommends realistic cost modeling in backtests and execution
optimization.

**Coverage limitation:** this specification does not claim to have
independently verified every full-text section, data set, equation, or
empirical result in this 2024 item. Use the paper as a conceptual prompt
and validate implementation details against AXEL's own broker fee
schedule and observed fills.

### What AXEL should take from it

-   Backtest results must be net of realistic fees and execution costs.
-   Live-vs-paper comparison should separate signal quality from
    execution quality.
-   Fees, spread, and slippage should be stored as distinct components
    wherever measurable.
-   A cost model must be calibrated to the instruments, venue, order
    types, and order sizes AXEL actually uses.
-   Do not add a generic fixed slippage assumption and then claim that
    it captures real market impact.

### Minimum cost ledger

For each order and each fill, preserve: - gross traded notional; -
commission; - exchange / regulatory / pass-through fees where
available; - fill price and quantity; - arrival reference price; -
bid/ask quote at arrival, if available; - quote timestamp and quote
source; - spread estimate at arrival, if quote data is valid; - explicit
slippage/implementation-shortfall metric; - realized position-level P&L
after fees, once closed; - cost-model version, if a modeled estimate is
also reported.

Avoid double counting. If the chosen implementation-shortfall figure
includes commissions, do not add the same commissions a second time to
derive net P&L. Maintain raw components and one documented aggregation
rule.

### Backtest and live reporting requirements

Every strategy report must distinguish: 1. gross strategy P&L; 2.
commissions and fees; 3. spread/explicit execution-cost estimates; 4.
slippage/implementation shortfall; 5. net P&L; 6. unfilled/cancelled
quantity and opportunity-cost diagnostics, where defined.

A backtest must use a realistic execution model and explicitly state
which costs are observed, modeled, or omitted. If data is absent, mark
it as an assumption; do not silently substitute zero.

------------------------------------------------------------------------

## 2.4 R4 --- Effective Trade Execution

### Research question and contribution

**\[SOURCE\]** Cesari, Marzo, and Zagaglia describe algorithmic trading
and high-frequency trading in modern markets, order types and their
characteristics, hidden/iceberg orders, order-book dynamics, transaction
costs, commonly used execution strategies, and optimal execution
including the Efficient Trading Frontier.

This is foundational market-microstructure material. It does not mean
AXEL should implement high-frequency trading or hidden-order tactics.
Phase 6 is explicitly a controlled tiny-live Stocks activation, not a
new strategy or market-making project.

### What AXEL should take from it

1.  **Order type is part of execution semantics.** Market, limit, stop,
    and time-in-force choices have different fill, price, and non-fill
    behavior. AXEL must not treat all order submissions as
    interchangeable.
2.  **Order-book liquidity matters.** A quoted price is not a guarantee
    that the desired quantity can be filled at that price.
3.  **Completion risk is real.** A passive limit order can fail to fill;
    a marketable order can incur spread and price impact.
4.  **Execution objectives conflict.** Minimizing cost, minimizing
    market exposure during execution, and guaranteeing completion are
    not the same objective.
5.  **Broker-specific details must stay inside an adapter.** Core logic
    should use AXEL's canonical order contract and not depend on
    provider-specific response shapes.

### Phase 6 order policy

-   Start with the smallest approved order and a narrow set of supported
    order types.
-   Explicitly define supported side, quantity, order type, limit price,
    time-in-force, session constraints, and any fractional-share
    behavior.
-   Reject unsupported combinations before sending them to the broker.
-   Never convert a rejected limit order into a market order
    automatically unless a separately reviewed policy explicitly
    authorizes that behavior.
-   Cancellation is asynchronous. A cancel request is not proof that the
    order was cancelled; fills can occur during the cancellation race.
-   Broker responses must be normalized into canonical states while
    preserving the raw provider response in restricted diagnostic
    storage if policy allows.
-   No smart order routing, multi-broker expansion, iceberg orders, or
    execution-learning strategy is required for Phase 6.

### Suggested canonical order intent

``` python
class OrderIntent:
    intent_id: UUID
    proposal_id: UUID
    approval_id: UUID
    section: Literal["STOCKS"]
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: Decimal | None
    time_in_force: Literal["DAY"]  # expand only after explicit review
    strategy_version: str
    risk_config_version: str
    decision_price: Decimal
    decision_timestamp: datetime
    idempotency_key: str
    correlation_id: UUID
```

This is a proposed contract, not a claim that these exact fields already
exist in the repository. OpenCode must inspect the existing contracts
first and extend them compatibly rather than duplicate them.

### Required validation

-   Decimal quantities/prices; no binary floating-point arithmetic for
    money.
-   Positive quantity; valid symbol; supported order type; valid
    tick/precision rules from broker/instrument metadata.
-   Limit price required for LIMIT and prohibited or ignored only
    according to explicit contract rules for MARKET.
-   Market/session and symbol eligibility checks.
-   Price and data freshness checks.
-   Risk and concentration checks.
-   Approval bound to the canonical order hash and configuration
    version.
-   No submission until the immutable pre-trade decision record has been
    written successfully.

------------------------------------------------------------------------

## 2.5 R5 --- Ernest P. Chan, Risk Management

### Source coverage and limits

**\[SOURCE\]** The publisher's table of contents identifies a Risk
Management chapter covering optimal leverage, constant proportion
portfolio insurance, stop loss, and risk indicators. This is a book
chapter rather than a standalone research paper in the source list.

This specification does **not** claim to reproduce the entire chapter or
endorse any single risk formula as suitable for AXEL. The Phase 6 PRD
itself governs live risk controls.

### What AXEL should take from it

-   Capital allocation and leverage are risk decisions, not agent
    preferences.
-   Stop-loss rules have to be implemented and tested as system
    behavior, not merely included in a strategy prompt.
-   Risk indicators should be observed continuously and used
    consistently.
-   Model risk and software risk are distinct: a strategy can be
    statistically wrong, and a correct strategy can still lose money due
    to faulty execution or infrastructure.
-   A theoretical optimal leverage or Kelly fraction is not a safe
    default for early live deployment when edge estimates are uncertain.

### Phase 6 deterministic risk policy

The live gate must enforce approved hard ceilings for: - total live
exposure; - per-position notional; - per-order notional; - daily loss; -
concurrent positions; - order frequency; - allowed symbols/universe; -
concentration; - strategy/playbook eligibility; - market/session
eligibility; - current data freshness; - kill-switch and Watchdog
health.

The exact numerical thresholds must come from reviewed, versioned
deployment configuration. This document intentionally does not invent
capital or loss-limit numbers.

### Fail-closed rule

If a required risk input is absent, stale, contradictory, unparseable,
or cannot be verified, reject the new risk-increasing order. Do not
infer a safe value. Do not allow an LLM to waive a limit.

A stop/kill path must be deterministic and independent of the
model-provider state. Define separately which read-only, reconciliation,
cancellation, and risk-reducing actions remain allowed during a halt; a
global "halt" must not accidentally destroy the ability to discover
exposure.

------------------------------------------------------------------------

## 2.6 R6 --- Optimizing Broker Performance Evaluation through Intraday Modeling of Execution Cost

### Research question and contribution

**\[SOURCE\]** Eisler and Muhle-Karbe present a method for evaluating
broker execution algorithms using trading data. The paper separates two
cost components: - a **linear cost** intended to quantify short-term
execution quality; - a **quadratic cost** associated with price impact.

The paper uses a transient-price-impact model and derives estimation
formulas. It also describes improving estimation accuracy by weighting
intraday price changes according to their expected impact content. The
authors report improved estimates and a framework for broker comparison.

### What AXEL should take from it

-   Execution costs should not be treated as one undifferentiated
    scalar.
-   Short-term execution quality and size-dependent price impact are
    conceptually different.
-   Intraday timing and the trajectory of order quantity matter when
    estimating impact.
-   A model that is useful for large orders may be statistically
    unstable or unnecessary for AXEL's tiny initial orders.

### Implementation decision

**Phase 6 minimum:** collect order-level timing, quantity trajectory,
fills, prices, and reference-market data. Report direct, interpretable
metrics first.

**Later research task:** estimate a linear short-term cost term and a
size/impact term only after sufficient clean observations exist. The
model must report assumptions, uncertainty, sample size, and
applicability limits.

Do not transplant the paper's equations into production risk controls
without: 1. reviewing the complete derivation and assumptions; 2.
verifying that the market, instrument, and order-size regime are
comparable; 3. creating synthetic tests and a reproducible offline
research notebook; 4. validating against held-out orders and simpler
baselines; 5. reviewing model output with a human; 6. keeping the model
out of the live authorization path during Phase 6.

------------------------------------------------------------------------

# 3. Combined engineering conclusions

The papers are complementary rather than interchangeable.

  -----------------------------------------------------------------------
  Research concern        Main source             AXEL implementation
                                                  consequence
  ----------------------- ----------------------- -----------------------
  Benchmark choice and    R1, R2                  Store multiple
  noisy execution costs                           benchmark metrics and
                                                  document each one

  Small samples /         R1                      Do not rank broker
  uncertainty                                     methods confidently
                                                  from a handful of fills

  Order placement and     R2, R4                  Preserve
  fill behavior                                   order-book/quote
                                                  context where
                                                  available; model
                                                  partial fills and
                                                  non-fills

  Realistic costs in      R1, R3                  Track commissions,
  strategy evaluation                             spread, slippage, and
                                                  net P&L without double
                                                  counting

  Order-type semantics    R4                      Use a canonical order
                                                  intent and strict
                                                  validation

  Capital and loss        R5 + P6                 Deterministic risk
  constraints                                     configuration, hard
                                                  caps, and stop behavior

  Distinguish execution   R6                      Capture the data needed
  quality from impact                             for later impact
                                                  analysis; do not
                                                  overfit early

  Safety and operational  P6                      Live gate, manual
  correctness                                     approval,
                                                  reconciliation,
                                                  Watchdog, halt,
                                                  rollback, audit trail
  -----------------------------------------------------------------------

**Highest-value Phase 6 insight:** AXEL cannot optimize what it does not
reliably observe. The first investment should be in correct order
lifecycle state, reliable broker reconciliation, and trustworthy
execution-cost data. More sophisticated models come after these
foundations.

------------------------------------------------------------------------

# 4. AXEL Phase 6 scope --- requirements to preserve

The existing Phase 6 PRD is authoritative for project scope. The
following are mandatory.

## 4.1 Goals

-   Connect the approved Stocks execution path to a supported broker.
-   Prove the order lifecycle from proposal through fill.
-   Enforce live-mode gates and manual approval.
-   Enforce tiny capital, position, loss, and order limits.
-   Reconcile broker state against AXEL continuously.
-   Detect incidents and halt safely.
-   Prove rollback and recovery.
-   Produce an auditable record of every live action.

## 4.2 Non-goals

-   No new Stocks strategy development.
-   No autonomous scaling.
-   No removal of manual approval.
-   No risk-limit relaxation.
-   No multi-broker expansion.
-   No activation of Crypto, Forex, Options, or Bets in Phase 6.

## 4.3 Preconditions

Before live activation: - Phase 5 paper-soak promotion gate passed and
evidence recorded. - Stocks strategy/playbook versions frozen. - Risk
configuration reviewed and versioned. - Broker account explicitly
designated for tiny-live. - API permissions are least privilege. -
Kill-switch and Watchdog drills pass. - Reconciliation tests pass. -
Manual approval workflow passes. - Rollback/halt runbook approved. -
Operators can access logs, alerts, and emergency controls.

## 4.4 Live configuration

Live mode must require explicit, versioned configuration including: -
`environment=LIVE`; - approved broker and account; -
`allowed_section=STOCKS`; - maximum live capital; - maximum position
notional; - maximum order notional; - maximum daily loss; - maximum
concurrent positions; - maximum order frequency; - allowed symbol
universe; - manual-approval requirement; - kill-switch state; -
configuration approval metadata.

Missing, stale, conflicting, invalid, or unapproved values fail closed.

## 4.5 Pre-trade gate sequence

For each order: 1. Validate request schema and strategy/playbook
eligibility. 2. Verify market session and symbol eligibility. 3. Verify
market data is sufficiently fresh. 4. Calculate deterministic risk and
check exposure/concentration. 5. Check daily loss/drawdown. 6. Check
duplicate, stale, or conflicting order intent. 7. Check kill-switch
state. 8. Verify Watchdog health. 9. Verify authenticated manual
approval, if required. 10. Write immutable audit record. 11. Submit only
after all required checks pass.

If any required check fails or cannot be completed, reject the new
order. Record a stable reason code and correlation ID.

## 4.6 Order lifecycle

The PRD includes states such as:

`CREATED → APPROVED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED`

and terminal/error paths including `CANCELLED`, `REJECTED`, `EXPIRED`,
and `ERROR`.

Implementation must also explicitly handle an **unknown/ambiguous
submission outcome** (for example, network timeout after the broker
accepted an order but before AXEL received the acknowledgment). Do not
blindly retry an ambiguous submit. Query broker state by deterministic
client order ID/idempotency key and reconcile before any retry decision.

## 4.7 Reconciliation

Continuously compare AXEL against broker state: - open orders; - fills
and partial fills; - positions; - cash/buying power; - realized and
unrealized P&L; - cancellations; - rejections.

Any unexplained mismatch becomes an incident. Never silently overwrite
one side to make the records match.

## 4.8 Halt and recovery

Support: - global halt; - Stocks-section halt; - halt on new
risk-increasing orders while preserving permitted
reconciliation/cancellation paths; - broker-disconnect halt; -
risk-limit halt; - Watchdog-triggered halt.

Recovery must require an explicit authority and evidence. Re-enabling
live must never happen automatically after restart or successful
reconnection. Return to disabled/paper state unless explicitly
reauthorized.

------------------------------------------------------------------------

# 5. Proposed architecture and module boundaries

OpenCode must inspect the repository before editing and use existing
patterns where they already exist. The following are logical
responsibilities, not an instruction to duplicate existing modules.

``` text
Agent / Analyst / Section Manager
             |
             | typed proposal only
             v
Deterministic Trading Core
  ├── proposal/schema validation
  ├── live configuration gate
  ├── manual approval verification
  ├── deterministic risk validation
  ├── idempotency / duplicate protection
  └── immutable decision audit
             |
             v
Execution Service
  ├── canonical order state machine
  ├── broker adapter interface
  ├── submit / cancel / query
  ├── normalized broker events
  └── durable event recording
             |
             v
Approved Broker Account

Independent Watchdog
  ├── health / heartbeat
  ├── risk and service health observations
  ├── independent halt authority
  └── durable halt evidence

Reconciliation Service
  ├── fetch broker orders/fills/positions/account
  ├── compare with AXEL records
  ├── create incidents on discrepancies
  └── block new risk-increasing orders when required

Execution Analytics (read-only)
  ├── per-fill cost metrics
  ├── order-level aggregates
  ├── benchmark/source metadata
  └── reports for operators and research
```

### Trust boundaries

-   Agent service must not receive broker credentials.
-   Agent service must not call broker submit/cancel APIs directly.
-   Trading Core must not import or depend on LLM SDKs.
-   Watchdog must not depend on an LLM call to halt.
-   Analytics and research models are read-only with respect to
    execution authorization.
-   Broker adapter normalizes provider-specific semantics;
    provider-specific response objects must not leak into risk logic.
-   Database roles should be least privilege; raw secrets must never
    enter logs, prompts, or business records.

------------------------------------------------------------------------

# 6. Canonical data contracts

Before adding schemas, OpenCode must inspect existing models and
migrations. Extend existing contracts rather than creating competing
definitions.

## 6.1 Order intent

Required semantic fields: - stable internal intent ID; - proposal ID and
strategy/playbook version; - section (`STOCKS`); - symbol; - side; -
quantity and order type; - limit price when applicable; -
time-in-force; - decision/arrival reference price and timestamp; - live
configuration version; - risk decision ID; - approval ID and approval
expiry; - idempotency key / deterministic client order ID; - correlation
ID; - creation timestamp.

Use `Decimal` or the repository's approved fixed-point representation
for prices, quantities, fees, and money. Avoid binary floating point for
financial values.

## 6.2 Order record

In addition to the intent: - current canonical state; - broker order
ID; - client order ID; - broker/account identifier (non-secret); -
submitted payload hash; - broker acknowledgment time; - last broker
update time; - cumulative filled quantity; - average fill price; -
remaining quantity; - cancel-request time and cancel result; - last
error category / stable reason code; - state version or event
sequence; - timestamps for created, approved, submitted, acknowledged,
partially filled, filled, cancelled, rejected, expired; - last
reconciliation time/status.

Do not overwrite history in a way that loses prior state transitions.
Prefer an append-only order-event history plus a current-state
projection.

## 6.3 Fill record

-   stable fill ID;
-   broker execution/fill ID;
-   internal order ID;
-   symbol and side;
-   fill quantity and price;
-   fill timestamp as supplied by broker;
-   received/ingested timestamp;
-   commission and fee components;
-   venue/route if available;
-   liquidity flag if broker provides one;
-   raw-event hash or source reference;
-   reconciliation status.

Deduplicate fills using the strongest broker execution identifier
available. If no unique identifier exists, use a carefully documented
composite key and reconciliation logic; do not assume timestamps alone
are unique.

## 6.4 Approval record

-   approval ID;
-   authenticated approver identity;
-   exact order/proposal hash;
-   risk configuration version;
-   issued and expiry timestamps;
-   decision (`APPROVED` / `REJECTED`);
-   reason/comment if required;
-   authentication/audit reference;
-   one-time-use/consumed timestamp.

An approval is not reusable for a materially changed order. If quantity,
symbol, side, price constraints, order type, strategy version, or risk
configuration changes, require a new approval.

## 6.5 Risk decision

-   risk decision ID;
-   order/intent ID;
-   allow/reject;
-   stable reason code(s);
-   all relevant measured exposures;
-   configured limits and config version;
-   data freshness evidence;
-   Watchdog health evidence;
-   kill-switch state;
-   approval verification outcome;
-   decision timestamp;
-   deterministic validator version.

Do not store only a boolean. The system must be able to explain why a
specific order was allowed or rejected.

## 6.6 Reconciliation result and incident

Reconciliation record: - run ID and timestamp; - broker/account; -
source snapshot times; - comparison counts; - mismatched entities and
fields; - severity; - resolution status; - linked incident ID.

Incident record: - incident ID; - category and severity; - first
detected time / last updated time; - affected order/account/position; -
observed vs expected values; - automatic action taken; - halt status; -
operator acknowledgment; - resolution notes; - re-enable approval and
evidence if applicable.

## 6.7 Execution metric record

-   metric ID;
-   order ID and/or fill ID;
-   metric name and version;
-   value and currency/unit;
-   side convention;
-   benchmark type;
-   benchmark price/window/source;
-   measurement timestamp;
-   quantity included;
-   cost components included/excluded;
-   data coverage/completeness;
-   `COMPUTABLE`, `PARTIAL_DATA`, or `NOT_COMPUTABLE`;
-   calculation code version.

------------------------------------------------------------------------

# 7. Order state machine and failure semantics

## 7.1 State principles

-   Every transition must be validated against a transition table.
-   Every transition is durably recorded before being treated as
    committed internally.
-   Broker events can arrive late or out of order; handle them
    idempotently.
-   A partial fill is not a terminal state.
-   Cancel requested is not the same as cancelled.
-   A timeout is not proof of failure.
-   A restart must reconstruct current state from durable records and
    broker reconciliation.
-   Never retry a submit merely because the client timed out.

## 7.2 Proposed states

  -------------------------------------------------------------------------
  State                   Meaning                 Allowed next states
                                                  (subject to broker facts
                                                  and policy)
  ----------------------- ----------------------- -------------------------
  `CREATED`               Intent persisted, not   `APPROVED`, `REJECTED`
                          yet approved/submitted  

  `APPROVED`              Exact order hash        `SUBMITTED`, `REJECTED`,
                          approved and still      `EXPIRED`
                          valid                   

  `SUBMITTED`             Submission request      `ACKNOWLEDGED`,
                          durably initiated       `PARTIALLY_FILLED`,
                                                  `FILLED`, `REJECTED`,
                                                  `UNKNOWN`, `ERROR`

  `UNKNOWN`               Submit outcome          `ACKNOWLEDGED`,
                          ambiguous; broker may   `PARTIALLY_FILLED`,
                          have accepted           `FILLED`, `CANCELLED`,
                                                  `REJECTED`, `ERROR` after
                                                  query/reconciliation

  `ACKNOWLEDGED`          Broker recognizes       `PARTIALLY_FILLED`,
                          order, no fill known    `FILLED`,
                          yet                     `CANCEL_PENDING`,
                                                  `CANCELLED`, `REJECTED`,
                                                  `EXPIRED`

  `PARTIALLY_FILLED`      Some quantity filled,   `PARTIALLY_FILLED`,
                          remainder may be live   `FILLED`,
                                                  `CANCEL_PENDING`,
                                                  `CANCELLED`, `REJECTED`,
                                                  `EXPIRED`

  `CANCEL_PENDING`        Cancellation requested, `PARTIALLY_FILLED`,
                          outcome not final       `FILLED`, `CANCELLED`,
                                                  `REJECTED`, `UNKNOWN`

  `FILLED`                Entire accepted         Terminal for that broker
                          quantity filled         order; corrections
                                                  handled as explicit
                                                  events

  `CANCELLED`             Broker confirms         Terminal for that order;
                          cancellation; some      retain filled quantity
                          quantity may already    
                          have filled             

  `REJECTED`              Broker or AXEL rejected Terminal unless a new
                          the order               intent is created

  `EXPIRED`               Broker confirms         Terminal
                          expiration              

  `ERROR`                 Non-recoverable         Recovery/reconciliation
                          internal/provider       path only; never silently
                          failure requiring       resubmit
                          controlled handling     
  -------------------------------------------------------------------------

This table is a proposed canonical model. Actual broker semantics must
be mapped to it and covered by contract tests.

## 7.3 Idempotent submission algorithm

1.  Validate intent and write the intent/order record.
2.  Generate deterministic client order ID from a stable internal
    identifier (do not generate a new one on retry).
3.  Persist `SUBMITTED` intent/outbox event transactionally before
    network submission, or use an equivalent reliable outbox pattern.
4.  Submit once through the broker adapter.
5.  If acknowledged, persist broker ID and normalized state.
6.  If timeout/connection loss occurs after submission may have reached
    the broker, set `UNKNOWN`.
7.  Query broker orders by client ID/order ID and reconcile before any
    retry.
8.  Retry only if the adapter and broker contract prove the original
    request was not accepted, or if the broker supports idempotent retry
    with the same key and that behavior is tested.
9.  Record every attempt and response with correlation ID and sanitized
    diagnostics.

A database transaction cannot make a remote broker API atomic with the
local database. The implementation must explicitly handle that
distributed-systems boundary.

## 7.4 Cancel/replace behavior

-   Persist the cancellation request before sending it.
-   Keep the order open until broker confirmation or authoritative
    reconciliation proves otherwise.
-   Continue ingesting fills during cancellation.
-   Never assume that cancellation eliminates already-executed quantity.
-   Replace orders only through a separately validated and approved
    policy; Phase 6 should default to a conservative, explicit flow.
-   A replace must not bypass exposure checks or approval binding.

------------------------------------------------------------------------

# 8. Live gate, manual approval, and deterministic risk

## 8.1 Live gate

Live activation is a distinct, auditable operator action. It must not be
triggered by an agent or by a default environment variable alone.

The gate checks: - Phase 5 evidence reference and status; - environment
is explicitly LIVE; - approved broker/account matches deployment; -
section is STOCKS; - reviewed configuration is current; - all numerical
caps are valid and internally consistent; - manual approval is enabled
for initial live operation; - Watchdog is healthy; - kill-switch is not
active; - broker read-only connectivity/account checks pass; - required
migrations and services are healthy; - rollback runbook and operator
access are confirmed.

Any missing prerequisite means live stays disabled.

## 8.2 Manual approval

-   Require an authenticated human action.
-   Bind approval to the canonical order hash, config version, and
    expiration time.
-   Consume approval once.
-   Recheck that approval has not expired or been invalidated at
    submission time.
-   Require new approval for a material order change.
-   Record approver, time, decision, and exact order version.
-   Agents cannot approve their own proposals.

## 8.3 Pre-trade risk validator

The deterministic validator must check, at minimum: - strategy/playbook
allowed for live; - symbol in approved universe; - valid trading
session; - market data freshness; - order type/quantity/price valid; -
maximum order notional; - maximum position notional; - total exposure; -
maximum concurrent positions; - concentration limits; - daily loss and
drawdown state; - order frequency; - duplicate/stale order; - existing
unresolved incident/halt; - broker connectivity; - Watchdog heartbeat; -
valid manual approval; - account/buying-power constraints where
available.

**Risk must not depend on LLM judgment.** If the validator cannot
reliably calculate a required field, it rejects new risk-increasing
orders.

------------------------------------------------------------------------

# 9. Broker adapter contract

OpenCode must identify the broker actually configured in the repository
before writing provider code. Do not assume a particular broker. Keep
provider-specific SDKs behind an interface.

Suggested logical interface:

``` python
class BrokerAdapter(Protocol):
    async def get_account(self) -> BrokerAccountSnapshot: ...
    async def get_positions(self) -> list[BrokerPosition]: ...
    async def get_open_orders(self) -> list[BrokerOrder]: ...
    async def get_order(self, broker_order_id: str) -> BrokerOrder | None: ...
    async def find_order_by_client_id(self, client_order_id: str) -> BrokerOrder | None: ...
    async def submit_order(self, request: BrokerOrderRequest) -> BrokerOrderAck: ...
    async def cancel_order(self, broker_order_id: str) -> BrokerCancelResult: ...
    async def list_fills(self, since: datetime | None) -> list[BrokerFill]: ...
    async def health_check(self) -> BrokerHealth: ...
```

The precise API and whether each method exists must be confirmed against
the selected provider. If the broker does not support lookup by client
ID or idempotent submission, design a documented conservative fallback
and test it.

### Adapter requirements

-   normalize provider-specific states and error codes;
-   apply timeouts and bounded retries to safe read-only calls;
-   distinguish retryable, non-retryable, and ambiguous submission
    errors;
-   sanitize credentials and response payloads before logging;
-   preserve provider timestamps and ingestion timestamps separately;
-   use explicit decimal parsing;
-   map exchange calendars and order restrictions correctly;
-   support read-only startup validation before any live order
    permission;
-   expose health state and rate-limit/backoff information;
-   never allow the adapter to bypass Trading Core approval.

------------------------------------------------------------------------

# 10. Reconciliation algorithm

## 10.1 Sources of truth

The broker is authoritative for what the broker accepted and executed.
AXEL is authoritative for its own proposals, approvals, risk decisions,
and audit history. Neither side should be silently overwritten to force
agreement.

## 10.2 Reconciliation loop

1.  Acquire current broker account snapshot, open orders, positions, and
    recent fills.
2.  Record snapshot time, broker/account, and ingestion time.
3.  Deduplicate broker events/fills.
4.  Match broker orders to AXEL orders using broker ID and deterministic
    client ID.
5.  Compare order state, requested quantity, filled quantity, average
    fill price, and remaining quantity.
6.  Compare positions and cash/buying power against AXEL's fill-derived
    ledger.
7.  Identify unknown broker orders, missing AXEL orders, missing fills,
    unexpected positions, quantity/price mismatches, and stale
    snapshots.
8.  Classify severity and create or update an incident.
9.  If discrepancy is risk-relevant or unexplained, block new
    risk-increasing orders and alert the operator.
10. Continue permitted read-only reconciliation and safe cancellation
    paths during a halt.
11. Resolve only with recorded evidence and explicit operator action
    where required.

## 10.3 Required mismatch cases

-   broker order exists but AXEL has no matching intent;
-   AXEL order is marked submitted but broker cannot find it;
-   broker reports fill that AXEL has not recorded;
-   AXEL fill quantity exceeds broker-reported fill quantity;
-   position differs from the fill-derived ledger;
-   broker account cash/buying power differs unexpectedly;
-   cancellation is recorded internally but broker order remains open;
-   broker reports a terminal state while AXEL still considers the order
    active;
-   snapshots are too old to establish a safe state.

Never "fix" a mismatch by deleting the broker order, overwriting fill
history, or editing the ledger without a reconciliation event and audit
record.

------------------------------------------------------------------------

# 11. Execution analytics specification

## 11.1 Capture before optimizing

At decision/arrival time, where the data provider makes it available,
capture: - best bid and ask; - quote timestamp; - last trade and
timestamp; - decision price; - symbol and session; - volatility/spread
features used by the strategy; - order size and notional; - side and
order type; - intended execution window; - strategy/playbook version.

At order/fill time, capture: - submit time; - broker acknowledgment
time; - fill event time; - event receive time; - quantity and price for
each fill; - fees; - partial fill sequence; - cancellation/rejection
events; - final completion/cancel time.

If quote/depth data is not available, the metric must say so. Do not
fabricate spread, queue position, market impact, or NS.

## 11.2 Minimum metrics

For each order: - fill ratio = filled quantity / requested quantity; -
average fill price; - time to first acknowledgment; - time to first
fill; - time to completion or terminal state; - total fees; - signed
implementation shortfall; - VWAP-relative slippage when a valid market
VWAP exists; - post-completion reversion at versioned horizons, if
reference prices exist; - cancel/reject rate; - number of retries and
ambiguous outcomes; - reconciliation delay and mismatch count.

For each reporting cohort: - order count; - total notional and filled
notional; - fill ratio distribution; - median and tail slippage; - total
and per-share cost; - results by symbol/liquidity bucket, side, order
type, time-of-day, volatility regime, strategy version, and broker; -
missing data and non-computable metric rates; - confidence interval or
uncertainty label where feasible.

Avoid aggregating only averages. Execution-cost distributions may be
skewed and heavy-tailed; report medians, quantiles, and sample sizes as
well.

## 11.3 Metric conventions

Every metric must specify: - side convention; - currency/unit; -
reference price; - reference timestamp; - window start/end; - whether
fees are included; - treatment of partial fills and unfilled quantity; -
source and completeness; - calculation version.

No chart or dashboard label should say "cost" if the metric is actually
signed slippage without fees. Use explicit names such as
`implementation_shortfall_per_share` and `fees_total`.

------------------------------------------------------------------------

# 12. Persistence and audit requirements

Use existing AXEL database and migration patterns. Do not introduce a
second competing source of truth.

Minimum durable entities or equivalent existing models: - live
configuration version and approval; - order intent/current state; -
append-only order event; - fill/execution; - manual approval; - risk
decision; - broker account/position snapshot; - reconciliation
run/result; - incident and resolution; - execution metric; - kill-switch
event; - audit event.

### Durability requirements

-   A broker event can be replayed without duplicating a fill.
-   Current order state can be reconstructed from persisted events plus
    reconciliation.
-   All order/approval/risk/audit records link through stable IDs.
-   Time fields preserve UTC and distinguish event time from receive
    time.
-   Configuration and strategy versions are immutable references.
-   Sensitive credentials are never stored in these records.
-   Any state mutation has actor/source, timestamp, correlation ID, and
    reason.
-   Database failure before submission must prevent the submission.
-   If the database becomes unavailable after a broker may have accepted
    an order, enter a controlled unknown/degraded state, halt new
    risk-taking, and reconcile after recovery.

------------------------------------------------------------------------

# 13. Incident management and kill-switch

## 13.1 Incident types required by the PRD

-   unexpected fill;
-   unknown broker order;
-   position mismatch;
-   stale market data;
-   broker outage;
-   repeated rejections;
-   excessive loss;
-   duplicate submission;
-   Watchdog failure;
-   database failure;
-   authentication/permission failure.

Add stable reason codes, severity, and deterministic actions. Do not
encode operational logic only in free-text messages.

## 13.2 Halt behavior

On a configured emergency condition: 1. persist the halt request/event
where possible; 2. block new risk-increasing orders immediately; 3.
notify the independent Watchdog and other execution workers; 4. cancel
outstanding orders only according to the reviewed halt policy and broker
semantics; 5. continue permitted order/fill/account queries and
reconciliation; 6. record which cancellations succeeded, failed, or
remain unknown; 7. alert the operator; 8. require explicit recovery
authorization before live orders can resume.

If the database is down, the implementation must have a safe
local/independent halt mechanism. Do not make the watchdog's ability to
halt depend exclusively on the database it may be responding to a
failure of.

## 13.3 Recovery

Recovery checklist: - verify kill-switch cause; - verify broker
connectivity; - reconcile open orders, fills, positions, cash, and
buying power; - verify no unknown orders remain; - resolve or explicitly
accept incidents; - review relevant logs and metrics; - confirm risk
configuration and strategy versions; - obtain authorized re-enable
approval; - start in disabled/paper state unless the explicit activation
process is completed again.

------------------------------------------------------------------------

# 14. Security and permissions

-   Broker secrets are accessible only to the execution process or
    approved secret broker.
-   No secrets in prompts, agent contexts, application logs, exception
    traces, metrics labels, or PR artifacts.
-   Least-privilege API keys; use read-only credentials for read-only
    checks if provider supports them.
-   Separate permissions for agents, Trading Core, execution service,
    Watchdog, dashboard, and analytics.
-   Agents cannot submit/cancel broker orders directly.
-   Dashboard visibility does not imply trading permission.
-   Production live configuration changes require review and audit.
-   Approval identity must be authenticated, not merely a string field
    submitted by a client.
-   All API endpoints enforce server-side authorization.
-   CI must detect imports of LLM libraries inside deterministic
    risk/execution safety modules if that is the existing architecture
    rule.

------------------------------------------------------------------------

# 15. Test plan --- required before tiny-live

## 15.1 Unit tests

-   order intent schema and canonical hash;
-   decimal arithmetic and notional calculation;
-   limit checks at exactly below/at/above boundaries;
-   risk exposure calculations;
-   daily loss limit;
-   symbol and session eligibility;
-   data freshness;
-   approval expiry and one-time consumption;
-   approval invalidation on material order changes;
-   state-transition validity;
-   fill deduplication;
-   implementation-shortfall sign convention for buys and sells;
-   fee aggregation without double counting;
-   missing-data handling for metrics.

## 15.2 Broker contract tests

-   account read;
-   position read;
-   open-order read;
-   submit accepted;
-   submit rejected;
-   order status lookup;
-   partial fill;
-   full fill;
-   cancellation accepted;
-   cancellation rejected or delayed;
-   fills received during cancel race;
-   client ID lookup;
-   rate limit;
-   provider timeout;
-   invalid credentials / insufficient permissions.

Use sandbox or paper endpoints wherever available. Never let CI default
to a live account.

## 15.3 Failure-injection tests

-   request times out before reaching broker;
-   broker accepts order but acknowledgment is lost;
-   duplicate acknowledgment arrives;
-   fill event is delivered twice;
-   fill arrives before acknowledgment;
-   partial fill followed by cancellation;
-   cancellation races with a full fill;
-   process crashes after broker acceptance but before local
    acknowledgment is stored;
-   database unavailable before submit;
-   database unavailable after submit;
-   Watchdog heartbeat missing;
-   stale quote;
-   broker disconnect;
-   malformed broker response;
-   kill-switch during active orders;
-   restart with open orders and partial fills.

For every case, assert: - no blind duplicate order; - no silent state
transition; - no false claim of cancellation; - risk-increasing orders
halt when required; - reconciliation can recover the authoritative
state; - audit trail remains understandable.

## 15.4 Security/permission tests

-   agent principal cannot access broker submit/cancel methods;
-   missing live configuration cannot activate live mode;
-   invalid configuration fails closed;
-   expired/reused approval rejected;
-   approval for a different order hash rejected;
-   watchdog can halt without LLM availability;
-   secret redaction tests;
-   live endpoints disabled by default in test and local environments.

## 15.5 Reconciliation tests

Intentionally inject mismatches: - broker-only order; - AXEL-only active
order; - missing fill; - duplicate fill; - position mismatch; -
cash/buying-power mismatch; - internal cancellation with broker order
still open.

Assert an incident is created, the mismatch is visible, and the required
risk block/halt occurs.

## 15.6 Research-metric tests

-   IS sign convention for buy and sell;
-   partial fills weighted by quantity;
-   fees counted exactly once;
-   benchmark window stable across reruns;
-   VWAP source/coverage recorded;
-   no benchmark if market data is missing;
-   NS tests only if NS is implemented and only in offline analytics;
-   metric calculation is reproducible from stored raw inputs.

------------------------------------------------------------------------

# 16. Suggested modular work queue for OpenCode

OpenCode should make each work package a separate, reviewable change
where practical. Before implementation, inspect the current tree,
models, migrations, API routes, broker integration, tests, and Phase 5
evidence. Reuse existing code instead of creating duplicate
abstractions.

  --------------------------------------------------------------------------------------
  Package           Deliverable                      Dependencies      Must not touch
  ----------------- -------------------------------- ----------------- -----------------
  `P6-AUDIT`        Repository audit; map current    None              No production
                    code to Phase 6 requirements;                      behavior changes
                    identify gaps                                      

  `P6-GATE`         Versioned fail-closed live       `P6-AUDIT`, Phase Do not relax
                    configuration and activation     5 evidence        Phase 5 gate
                    check                            contract          

  `P6-APPROVAL`     Authenticated, expiring,         Existing auth and No agent
                    single-use approval bound to     order contract    self-approval
                    order hash                                         

  `P6-BROKER`       Canonical adapter and provider   `P6-AUDIT`        No provider SDK
                    contract tests                                     leakage into risk
                                                                       core

  `P6-ORDER`        Durable state machine,           `P6-BROKER`,      No blind retry or
                    idempotency, unknown-submit      persistence       implicit market
                    handling                         contract          conversion

  `P6-RISK`         Deterministic pre-trade checks   `P6-GATE`, order  No LLM risk
                    and hard caps                    intent            dependency

  `P6-RECON`        Orders/fills/positions/account   `P6-BROKER`,      Never silently
                    reconciliation and incident      `P6-ORDER`        overwrite
                    creation                                           mismatches

  `P6-HALT`         Independent halt behavior and    Watchdog +        Do not disable
                    explicit recovery                incident model    emergency
                                                                       reconciliation

  `P6-OBS`          Order/fill telemetry, cost       Durable           No metrics may
                    metrics, operational dashboard   order/fill data   authorize trades
                    fields                                             

  `P6-QA`           Contract, property,              All above         CI must not
                    failure-injection, permission,                     access live
                    reconciliation tests                               trading

  `P6-RUNBOOK`      Deploy, halt, reconcile,         Operational       Recovery cannot
                    recover, rollback documentation  behavior stable   auto-enable live

  `P6-ROLLOUT`      Controlled tiny-live checklist   All above + Phase No scaling or
                    and evidence package             5 passed          risk relaxation
  --------------------------------------------------------------------------------------

### Recommended parallelism

Can run in parallel after `P6-AUDIT`: - `P6-GATE` and `P6-BROKER` if
they agree on the config and adapter contracts; - `P6-APPROVAL` and
initial `P6-OBS` schema work; - `P6-RUNBOOK` drafting while engineering
work proceeds.

Should be sequential or contract-first: - `P6-ORDER` requires broker
adapter semantics and durable identifiers; - `P6-RECON` requires the
order/fill data model; - `P6-HALT` needs incident and service-health
semantics; - final `P6-QA` must test the integrated system, not just
mocks; - `P6-ROLLOUT` is blocked until all mandatory tests and Phase 5
evidence are complete.

------------------------------------------------------------------------

# 17. Definition of done

Phase 6 is not complete until all applicable statements are true:

-   [ ] Phase 5 promotion evidence is verified and linked.
-   [ ] Live configuration is disabled by default and fails closed when
    incomplete.
-   [ ] Broker account and permissions are explicitly approved for
    tiny-live.
-   [ ] Broker adapter passes contract tests.
-   [ ] The order state machine covers acknowledgments, partial fills,
    cancellations, rejections, expirations, unknown outcomes, and
    restarts.
-   [ ] Duplicate submission is prevented or safely resolved through
    documented broker-specific idempotency/reconciliation behavior.
-   [ ] Manual approval is authenticated, expiring, single-use, and
    bound to exact order/config versions.
-   [ ] All required pre-trade checks run deterministically before
    submission.
-   [ ] Agent processes cannot submit broker orders or access
    credentials.
-   [ ] Broker-to-AXEL reconciliation detects intentional mismatches.
-   [ ] Unknown orders and unexplained position/cash mismatches create
    incidents and block new risk-taking according to policy.
-   [ ] Kill-switch and Watchdog drills pass, including relevant failure
    scenarios.
-   [ ] Order/fill metrics can be reproduced from stored raw inputs.
-   [ ] Dashboard reports real account/order/position/risk data rather
    than simulated values.
-   [ ] Incident, halt, rollback, and recovery runbooks are tested.
-   [ ] Initial tiny-live order is manually approved, fully auditable,
    and reconciled.
-   [ ] No automatic capital scaling is introduced.

## Acceptance test from the Phase 6 PRD

From a clean deployment with live disabled: 1. An operator cannot submit
an order. 2. After valid approved configuration and authenticated manual
approval, the smallest approved Stocks order passes deterministic
validation. 3. The order reaches the broker and its lifecycle is
captured. 4. The fill is reconciled with the broker. 5. A simulated
mismatch creates an incident. 6. A kill-switch event stops new
risk-increasing orders. 7. Recovery returns to disabled/paper state
until explicit reauthorization.

------------------------------------------------------------------------

# 18. Implementation order --- maximize efficiency without increasing risk

1.  **Audit current repository and Phase 5 evidence.** Produce a gap
    table before coding. Do not assume the PRD's planned modules are
    missing or present without checking.
2.  **Freeze contracts.** Agree on canonical order intent, order state,
    fill, approval, risk decision, incident, and broker adapter
    interfaces.
3.  **Implement fail-closed live gate and manual approval.** Prove live
    mode cannot be activated accidentally.
4.  **Implement broker adapter in read-only mode first.** Validate
    account, positions, open orders, permissions, clock/timezone
    handling, and API limits.
5.  **Implement order state machine and idempotency.** Focus on
    ambiguous outcomes, partial fills, cancel races, restarts, and event
    deduplication.
6.  **Implement deterministic pre-trade risk.** Test every limit at
    boundary conditions.
7.  **Implement reconciliation and halt/recovery.** Deliberately inject
    mismatches and prove new risk-taking is blocked.
8.  **Implement execution-cost capture.** Start with transparent metrics
    and raw data preservation. Do not start with Bayesian or impact
    models.
9.  **Integrate dashboard, alerts, and runbook.** All operational
    metrics must come from real persisted state.
10. **Run integrated failure drills.** Only after they pass should
    operators consider the smallest live order.
11. **Observe before optimizing.** Collect reliable fills, fees, quotes,
    and outcomes. Only then evaluate whether Bayesian TCA, Negative
    Selection, or intraday impact models are justified.
12. **No automatic scaling.** Any later increase in live capital is a
    separately reviewed promotion decision, not an automatic result of a
    profitable run.

------------------------------------------------------------------------

# 19. Explicit non-goals for OpenCode

OpenCode must not: - create new Stocks alpha strategies as part of Phase
6; - use an LLM to authorize, size, or bypass a trade; - remove the
Phase 5 gate; - invent numeric capital, order, drawdown, or daily-loss
limits; - automatically scale capital after profits; - add multi-broker
routing or advanced execution ML as a Phase 6 requirement; - assume a
broker's submit timeout means the order was not accepted; -
automatically turn a failed limit order into a market order; - treat a
cancellation request as a confirmed cancellation; - suppress
reconciliation mismatches by overwriting records; - count unknown or
missing execution costs as zero; - leak broker secrets into logs,
prompts, telemetry, or Git; - enable live trading in CI, local
development, or default deployment; - implement retrospective metrics
such as Negative Selection in the live authorization path; - mark Phase
6 complete based on compilation or unit tests alone.

------------------------------------------------------------------------

# 20. OpenCode kickoff prompt

Paste the following prompt into OpenCode after placing this Markdown
file and the existing Phase 6 PRD in the repository/docs context.

> You are implementing AXEL v2 Phase 6 --- Tiny Live Stocks. Read
> `AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md` and the existing Phase 6
> PRD in full before editing code.
>
> **First task: audit only.** Inspect the repository, migrations,
> current order/risk/execution code, broker integration, Watchdog,
> dashboard/API, tests, CI, and Phase 5 promotion evidence. Produce a
> table mapping each Phase 6 requirement to existing file(s), current
> status (`DONE`, `PARTIAL`, `MISSING`, `BLOCKED`), evidence, and the
> smallest required change. Do not guess from filenames.
>
> Preserve AXEL's trust boundary: agents/LLMs propose; deterministic
> Trading Core validates; independent Watchdog can halt. Do not add a
> direct Agent-to-broker path. Do not change risk limits or invent
> numeric limits. Do not bypass Phase 5.
>
> After the audit, implement in separate modular work packages: live
> gate and approval; broker adapter; durable order state machine and
> idempotency; deterministic pre-trade risk; broker reconciliation;
> incidents and halt/recovery; execution telemetry; tests and runbook.
> Reuse existing models and interfaces where possible. Before adding a
> new abstraction, explain why the current one is insufficient.
>
> Every change must include tests. Prioritize failure tests: broker
> accepts but response times out, duplicate fill, partial fill,
> cancel/fill race, restart with open order, database outage, Watchdog
> failure, stale quote, unknown broker order, and position mismatch.
>
> Do not submit any live order during development or CI. Keep live
> disabled by default. No Phase 6 completion claim is valid until Phase
> 5 evidence is verified, all required
> contract/failure/reconciliation/permission tests pass, and the
> acceptance test is demonstrated.
>
> Start by returning the audit and proposed file-level change plan. Wait
> for review before making broad changes.

------------------------------------------------------------------------

# 21. Final recommendation

For AXEL's first real-money phase, the most efficient path is:

**Reliable state and reconciliation → deterministic safety gate →
measurable execution costs → controlled tiny-live evidence → only then
advanced execution research.**

R1 and R6 justify building the data foundation for better cost
estimation. R2 is useful as a later post-trade research metric, not a
predictive/live control. R3 reinforces realistic cost accounting, but
the accessible source coverage here is limited to the abstract. R4
informs correct order semantics and market-microstructure awareness. R5
reinforces that capital allocation and loss controls belong to explicit
risk management, not agent improvisation. The existing Phase 6 PRD
determines the hard operational gates.

The objective is not to maximize the number of trades or optimize a
headline backtest. It is to maximize **risk-adjusted, net-of-cost
performance while preserving the ability to explain, reconcile, and
safely stop every live action**.
