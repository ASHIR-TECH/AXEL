# Phase 7 Implementation — Crypto Vertical

Status: **Code Complete & Verified** (588 tests passing repository-wide, zero boundary lint violations, 19/19 Phase 7 tests passing).  
Controlling Specification: `docs/AXEL_Phase_7_PRD.pdf`.

---

## 1. Scope Delivered

Phase 7 builds the **Crypto vertical** on top of AXEL's deterministic core. It adds crypto-specific market data, funding rates, on-chain analytics, features, creative analyst agents, section risk controls, backtesting, paper trading, and a promotion gate.

Creative agents propose; deterministic risk controls decide whether anything reaches execution.

### Non-Negotiable Phase 7 Invariants
1. **No-Leverage Rule**: The initial Crypto implementation operates strictly with zero leverage (`leverage <= 1.0`). Any leverage or margin request fails closed.
2. **Rolling 24-Hour UTC Stop**: Enforces a rolling 24-hour UTC loss limit (`3.0%`). Breach deterministically halts new Crypto risk.
3. **Independent Venue Risk**: Venues (e.g. Binance, Bybit, OKX) can be independently disabled on latency spikes, maintenance, or stale data without halting other venues or sections.
4. **Honest Net Edge**: Arbitrage spreads are accepted only if net expected edge remains positive after realistic taker fees, spread, slippage, settlement, and counterparty risk haircuts.
5. **One-Leg Failure Handling**: Simultaneous fills are never assumed; broken arbitrage legs enter an immediate repair/hedge/incident path.
6. **Untrusted Data Boundary**: Unverified on-chain wallet labels are never treated as facts (confidence halved or disqualified). Zero LLM imports exist in risk or execution paths.

---

## 2. PRD Work Package → Code Architecture

| PRD Section | Module(s) | Key Deliverables |
| :--- | :--- | :--- |
| **P7-SCHEMAS** | [`axel/crypto/schemas.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/schemas.py) | `CryptoInstrument`, `FundingRateRecord`, `CryptoOrderBookRecord`, `OnChainObservation`, `CryptoFeatureRecord`, `CryptoVenueHealth`, `ArbitrageSpreadOpportunity` |
| **P7-ADAPTER** | [`axel/crypto/adapters/base.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/adapters/base.py), [`mock_exchange.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/adapters/mock_exchange.py) | Common `CryptoExchangeAdapter` interface; built-in no-leverage & withdrawal guards; high-fidelity in-memory multi-venue simulator (`MockCryptoExchange`) |
| **P7-FEAT** | [`axel/crypto/features/engine.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/features/engine.py) | `CryptoFeatureEngine` covering 8 feature families: returns, realized vol, liquidity/depth, funding/basis, cross-spreads, flow, on-chain, and regime |
| **P7-RISK** | [`axel/crypto/risk/no_leverage.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/risk/no_leverage.py), [`rolling_utc_stop.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/risk/rolling_utc_stop.py), [`venue_risk.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/risk/venue_risk.py) | `NoLeverageEnforcer`, `Rolling24HourUtcStop` (persisted to `.axel_crypto_stop.json`), `VenueRiskManager` (independent venue circuit breaking) |
| **P7-ANALYSTS** | [`axel/crypto/analysts/`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/analysts/) | `FundingAnalyst`, `OnChainAnalyst`, `MarketMicrostructureAnalyst`, `RegimeRiskAnalyst`, and `CryptoSectionManager` |
| **P7-STRAT** | [`axel/crypto/strategies/`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/strategies/) | `FundingArbitrageStrategy`, `CrossExchangeArbitrageStrategy`, `OneLegFailureHandler`, `OnChainFlowStrategy` |
| **P7-BT** | [`axel/crypto/backtest/backtester.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/backtest/backtester.py) | `CryptoBacktester` with honest 8-hour funding payment accounting, taker/maker fee models, slippage, and walk-forward OOS metrics |
| **P7-PAPER** | [`axel/crypto/paper/paper_engine.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/paper/paper_engine.py), [`promotion_gate.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/paper/promotion_gate.py) | `CryptoPaperEngine` (2-leg execution without capital movement), `CryptoPromotionGate` (evaluates 10 promotion criteria) |
| **P7-OBS** | [`axel/crypto/observability/metrics.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/observability/metrics.py) | `CryptoMetricsCollector` & `CryptoObservabilitySnapshot` |
| **P7-CORE** | [`axel/risk/engine.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/risk/engine.py), [`axel/db/models.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/db/models.py) | Integrated rolling 24h stop and venue risk hooks in `SectionRiskAgent.evaluate()`; added ORM tables for funding rates, order books, on-chain metrics, and venue health |

---

## 3. Data Contracts and Point-in-Time Integrity

Every record carries provenance and strictly enforces point-in-time availability:
* `available_at >= event_time`: No look-ahead is permitted in backtesting or signal generation.
* `FundingRateRecord`: Captures perpetual rate, interval (8h), predicted rate, mark price, and index price.
* `CryptoOrderBookRecord`: Captures best bids/asks, spread, mid price, and depth at top-N levels in USD.
* `OnChainObservation`: Captures block number, timestamp, entity address, metric type, value, and verification status (`is_verified_label`). Unverified labels are never treated as facts.

---

## 4. Playbook Strategies & Honest Cost Accounting

### 4.1 Funding Arbitrage (`FundingArbitrageStrategy`)
* Evaluates funding differentials between perpetual contracts or spot-perpetual carry.
* Computes net expected edge after deducting:
  $$\text{Net Edge} = \text{Expected Funding} - (\text{Taker Fees} \times 4 + \text{Half Spread} \times 4 + \text{Slippage} \times 4 + \text{Settlement} + \text{Venue Risk Haircut})$$
* Any spread where $\text{Net Edge} \le 0$ is rejected.

### 4.2 Cross-Exchange Arbitrage (`CrossExchangeArbitrageStrategy`)
* Synchronizes timestamps between Venue A and Venue B (rejects skew $> 2\text{s}$).
* Checks available liquidity on both books.
* Accounts for taker fees on both legs, slippage, network latency buffer, and settlement costs.
* Broken Leg Handling: [`OneLegFailureHandler`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/strategies/cross_exchange_arbitrage.py) catches when Leg 1 fills but Leg 2 fails, generates a `CRITICAL` incident, and triggers an immediate `MARKET_HEDGE` or `CANCEL_AND_UNWIND`.

### 4.3 On-Chain Flow Strategy (`OnChainFlowStrategy`)
* Evaluates exchange net outflow (coins moved to cold storage) as bullish accumulation.
* Requires verified entity labels; unverified labels reduce confidence below the trade threshold and cause the strategy to abstain.

---

## 5. Deterministic Section Risk Controls

1. **No-Leverage Enforcer** ([`axel/crypto/risk/no_leverage.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/risk/no_leverage.py)):
   * Rejects any `leverage > 1.0` or margin parameter with `NoLeverageViolationError`.
2. **Rolling 24-Hour UTC Stop** ([`axel/crypto/risk/rolling_utc_stop.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/risk/rolling_utc_stop.py)):
   * Computes realized P&L + unrealized P&L - fees over the rolling 24h UTC window.
   * Breaching 3% loss trips a persistent section halt (`.axel_crypto_stop.json`).
   * Re-arming requires an authenticated operator token and written rationale.
3. **Venue Risk Manager** ([`axel/crypto/risk/venue_risk.py`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/risk/venue_risk.py)):
   * Tracks latency, maintenance, stale data ($>15\text{s}$), and order rejection spikes ($>5\%$).
   * Can independently disable a venue while allowing other venues and sections to operate.

---

## 6. Verification and Acceptance Testing

All 19 Phase 7 tests pass in [`tests/crypto/`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/tests/crypto/):
* `test_acceptance.py`: Runs the complete end-to-end acceptance sequence from the PRD.
* `test_crypto_data.py`: Validates instruments, order books, and point-in-time lookahead checks.
* `test_crypto_risk.py`: Validates no-leverage, rolling 24h stop, and independent venue circuit breaking.
* `test_crypto_strategies.py`: Validates net edge rejection, one-leg failure handling, and on-chain filtering.
* `test_crypto_analysts.py`: Validates signal generation and Section Manager consensus gating.
* `test_crypto_backtest.py`: Validates funding accrual, fee drag, and expectancy metrics.
* `test_crypto_paper_and_gate.py`: Validates paper execution and 10-point promotion gate evaluation.
