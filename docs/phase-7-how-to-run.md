# Phase 7 — Crypto Vertical: How It Works and How to Run It

This runbook guides operators on configuring, testing, backtesting, and paper trading the Crypto vertical (Phase 7).

---

## 1. Safety Principles Before Starting

* **No Leverage:** The Crypto implementation strictly operates with cash spot and 1x unleveraged perpetuals. Any leverage parameter (`> 1.0`) is rejected fail-closed.
* **Rolling 24-Hour UTC Stop:** Trading halts deterministically if net loss over the rolling 24-hour UTC window exceeds `3.0%`.
* **Zero Capital Movement:** Initial validation and paper trading run without real money movement.

---

## 2. Running Verification and Tests

To run the complete verification suite for Phase 7:

```bash
# 1. Run all Phase 7 crypto tests
.venv/bin/python -m pytest tests/crypto/ -v

# 2. Run the PRD acceptance test
.venv/bin/python -m pytest tests/crypto/test_acceptance.py -v

# 3. Verify zero LLM imports in risk and execution paths
.venv/bin/python scripts/check_no_llm_imports.py

# 4. Run repository-wide lint check
.venv/bin/python -m ruff check axel tests scripts
```

All 19 Phase 7 tests and 588 repository-wide tests must be green.

---

## 3. Running a Crypto Backtest

To run the cost-aware perpetual funding carry backtest:

```python
from decimal import Decimal
from datetime import UTC, datetime, timedelta
from axel.crypto.backtest.backtester import CryptoBacktester
from axel.crypto.schemas import FundingRateRecord
from axel.data.schemas import BarRecord, Provenance

# Initialize backtester with realistic VIP taker fees and slippage
backtester = CryptoBacktester(
    initial_capital=Decimal("100000.0"),
    taker_fee_bps=Decimal("2.0"),      # 0.02%
    slippage_bps=Decimal("1.0"),       # 0.01%
)

# Run backtest over bars and funding records
result = backtester.run_perpetual_carry_backtest(
    bars=bars,
    funding_records=funding_records,
    position_size_usd=Decimal("10000.0"),
)

print(f"Total Trades: {result.total_trades}")
print(f"Hit Rate: {result.hit_rate:.1%}")
print(f"Total Net PnL: ${result.total_net_pnl:,.2f}")
print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
print(f"Expectancy in R: {result.avg_expectancy_r:.4f}R")
```

---

## 4. Paper Trading Execution

To simulate multi-venue arbitrage execution in paper mode:

```python
from decimal import Decimal
from axel.crypto.adapters.mock_exchange import MockCryptoExchange
from axel.crypto.paper.paper_engine import CryptoPaperEngine
from axel.crypto.strategies.funding_arbitrage import FundingArbitrageStrategy

exchange_a = MockCryptoExchange(venue_name="binance")
exchange_b = MockCryptoExchange(venue_name="bybit")

paper_engine = CryptoPaperEngine(adapters={"binance": exchange_a, "bybit": exchange_b})

# Evaluate opportunity
strat = FundingArbitrageStrategy()
opp = strat.evaluate_spread(funding_a, funding_b, available_liquidity_usd=Decimal("50000.0"))

if opp.is_profitable:
    result = paper_engine.execute_paper_arbitrage(
        opportunity=opp,
        qty=Decimal("0.5"),
        risk_decision=risk_decision,
    )
    print(f"Fully Hedged: {result.is_fully_hedged}, Net PnL: ${result.net_pnl_usd:,.2f}")
```

---

## 5. Monitoring Venue Health and Tripping Circuit Breakers

The [`VenueRiskManager`](file:///C:/Users/Muhammad/Desktop/Open-source/Axel/AXEL/axel/crypto/risk/venue_risk.py) protects the system against venue degradation:

```python
from axel.crypto.risk.venue_risk import VenueRiskManager
from axel.crypto.schemas import CryptoVenueHealth
from axel.crypto.types import VenueStatus

manager = VenueRiskManager()

# Update health from telemetry
manager.update_health(CryptoVenueHealth(
    venue="bybit",
    status=VenueStatus.ACTIVE,
    latency_ms=45.0,
    maintenance_mode=False,
    last_heartbeat=datetime.now(UTC),
    stale_data_seconds=1.0,
))

# Manually disable a venue if suspicious balance drift or API instability occurs:
manager.disable_venue("bybit", "Suspected API balance drift under review")

# Re-enable when resolved:
manager.enable_venue("bybit")
```

---

## 6. Evaluating the Promotion Gate

Before promoting any strategy-venue pair from paper to live readiness:

```python
from axel.crypto.paper.promotion_gate import CryptoPromotionEvidence, CryptoPromotionGate

evidence = CryptoPromotionEvidence(
    strategy_id="crypto:funding_arbitrage",
    venue="binance",
    oos_backtest_passed=True,
    oos_sharpe=1.25,
    trade_count=150,
    parameter_robustness_passed=True,
    no_leakage_audit_passed=True,
    paper_trade_count=80,
    paper_net_edge_positive=True,
    arbitrage_leg_completion_rate=0.98,
    unresolved_reconciliation_breaks=0,
    venue_uptime_pct=0.999,
    venue_avg_latency_ms=45.0,
    no_leverage_verified=True,
    rolling_24h_utc_stop_passed=True,
    killswitch_watchdog_passed=True,
    human_signoff_recorded=True,
    human_reviewer="lead_quant_operator",
)

decision = CryptoPromotionGate.evaluate(evidence)
print(f"Promoted: {decision.promoted}")
if not decision.promoted:
    print(f"Blocking Reasons: {decision.reasons}")
```
