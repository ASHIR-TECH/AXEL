"""
Tests for Phase 7 Crypto Playbook Strategies:
- Funding-Rate Arbitrage
- Cross-Exchange Arbitrage & One-Leg Failure Handling
- On-Chain Flow Strategy
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from axel.crypto.schemas import (
    CryptoOrderBookRecord,
    FundingRateRecord,
    OnChainObservation,
)
from axel.crypto.strategies.cross_exchange_arbitrage import (
    ArbitrageLegExecution,
    CrossArbCostModel,
    CrossExchangeArbitrageStrategy,
    OneLegFailureHandler,
)
from axel.crypto.strategies.funding_arbitrage import (
    FundingArbCostModel,
    FundingArbitrageStrategy,
)
from axel.crypto.strategies.on_chain_flow import (
    OnChainFlowStrategy,
    OnChainStrategyParameters,
)
from axel.crypto.types import ArbitrageLegStatus, OnChainMetricType, OneLegFailureAction
from axel.data.schemas import Provenance


def test_funding_arbitrage_strategy():
    now = datetime.now(UTC)
    prov = Provenance(source="test", source_id="1", source_hash="h1", ingestion_timestamp=now)

    # Low-cost model for testing
    cost_model = FundingArbCostModel(
        taker_fee_bps=Decimal("2.0"),
        half_spread_bps=Decimal("1.0"),
        slippage_bps=Decimal("1.0"),
        settlement_cost_bps=Decimal("0.5"),
        venue_risk_haircut_bps=Decimal("1.0"),
    )
    # Round trip cost = 4 * (2+1+1) + 0.5 + 1.0 = 17.5 bps
    strat = FundingArbitrageStrategy(cost_model=cost_model)

    # 1. Large funding rate spread: Venue A = 0.01% (1 bps), Venue B = 0.08% (8 bps)
    # diff = 7 bps per 8h. Over 3 cycles = 21 bps expected funding.
    # Net edge = 21 - 17.5 = +3.5 bps -> PROFITABLE
    f_a = FundingRateRecord(venue="venue_a", symbol="BTC/USDT:USDT", event_time=now, available_at=now, rate=Decimal("0.0001"), provenance=prov)
    f_b = FundingRateRecord(venue="venue_b", symbol="BTC/USDT:USDT", event_time=now, available_at=now, rate=Decimal("0.0008"), provenance=prov)

    opp = strat.evaluate_spread(f_a, f_b, available_liquidity_usd=Decimal("50000.0"))
    assert opp.is_profitable
    assert opp.net_edge_bps > Decimal("0.0")
    assert opp.rejection_reason is None

    # 2. Deliberately unprofitable spread: Venue A = 0.01%, Venue B = 0.02%
    # diff = 1 bps. Over 3 cycles = 3 bps. Costs = 17.5 bps -> Net edge = -14.5 bps -> REJECTED
    f_c = FundingRateRecord(venue="venue_c", symbol="BTC/USDT:USDT", event_time=now, available_at=now, rate=Decimal("0.0002"), provenance=prov)
    opp_unprofitable = strat.evaluate_spread(f_a, f_c, available_liquidity_usd=Decimal("50000.0"))
    assert not opp_unprofitable.is_profitable
    assert "Net edge" in (opp_unprofitable.rejection_reason or "")

    # 3. Clock skew > 60s -> REJECTED
    f_stale = FundingRateRecord(venue="venue_stale", symbol="BTC/USDT:USDT", event_time=now - timedelta(seconds=75), available_at=now - timedelta(seconds=75), rate=Decimal("0.0008"), provenance=prov)
    opp_skew = strat.evaluate_spread(f_a, f_stale, available_liquidity_usd=Decimal("50000.0"))
    assert not opp_skew.is_profitable
    assert "Timestamp skew" in (opp_skew.rejection_reason or "")


def test_cross_exchange_arbitrage_and_one_leg_failure():
    now = datetime.now(UTC)
    prov = Provenance(source="test", source_id="1", source_hash="h1", ingestion_timestamp=now)

    cost_model = CrossArbCostModel(
        taker_fee_buy_bps=Decimal("4.0"),
        taker_fee_sell_bps=Decimal("4.0"),
        slippage_bps_leg_a=Decimal("2.0"),
        slippage_bps_leg_b=Decimal("2.0"),
        latency_risk_bps=Decimal("2.0"),
        settlement_cost_bps=Decimal("1.0"),
    )
    # Total costs = 15 bps
    strat = CrossExchangeArbitrageStrategy(cost_model=cost_model)

    # Book A: Ask = 50,000 (buy venue)
    book_a = CryptoOrderBookRecord(
        venue="venue_a",
        symbol="BTC/USDT",
        event_time=now,
        available_at=now,
        bids=((Decimal("49990.0"), Decimal("2.0")),),
        asks=((Decimal("50000.0"), Decimal("2.0")),),  # Buy price = 50,000
        provenance=prov,
    )
    # Book B: Bid = 50,200 (sell venue). Spread = 200 / 50000 = 40 bps
    # Net edge = 40 bps - 15 bps = +25 bps -> PROFITABLE
    book_b = CryptoOrderBookRecord(
        venue="venue_b",
        symbol="BTC/USDT",
        event_time=now,
        available_at=now,
        bids=((Decimal("50200.0"), Decimal("2.0")),),  # Sell price = 50,200
        asks=((Decimal("50210.0"), Decimal("2.0")),),
        provenance=prov,
    )

    opp = strat.evaluate_spread(book_a, book_b)
    assert opp.is_profitable
    assert opp.net_edge_bps == Decimal("25.0")

    # Tight spread: Bid on B = 50,050. Spread = 10 bps - 15 bps = -5 bps -> REJECTED
    book_c = CryptoOrderBookRecord(
        venue="venue_c",
        symbol="BTC/USDT",
        event_time=now,
        available_at=now,
        bids=((Decimal("50050.0"), Decimal("2.0")),),
        asks=((Decimal("50060.0"), Decimal("2.0")),),
        provenance=prov,
    )
    opp_tight = strat.evaluate_spread(book_a, book_c)
    assert not opp_tight.is_profitable
    assert "Net edge" in (opp_tight.rejection_reason or "")

    # One-Leg Failure Handling:
    # Leg A (Buy) filled 1.0 BTC, Leg B (Sell) rejected / failed
    leg_a = ArbitrageLegExecution(
        venue="venue_a",
        symbol="BTC/USDT",
        side="buy",
        requested_qty=Decimal("1.0"),
        filled_qty=Decimal("1.0"),
        price=Decimal("50000.0"),
        status=ArbitrageLegStatus.FILLED,
    )
    leg_b = ArbitrageLegExecution(
        venue="venue_b",
        symbol="BTC/USDT",
        side="sell",
        requested_qty=Decimal("1.0"),
        filled_qty=Decimal("0.0"),
        price=Decimal("50200.0"),
        status=ArbitrageLegStatus.FAILED,
        error="Exchange timeout",
    )

    incident = OneLegFailureHandler.handle_failure(leg_a, leg_b, now=now)
    assert incident.incident_severity == "CRITICAL"
    assert incident.unhedged_qty == Decimal("1.0")
    assert incident.recommended_action == OneLegFailureAction.MARKET_HEDGE


def test_on_chain_flow_strategy():
    now = datetime.now(UTC)
    prov = Provenance(source="glassnode", source_id="1", source_hash="h1", ingestion_timestamp=now)
    strat = OnChainFlowStrategy(OnChainStrategyParameters(min_confidence=0.60, min_net_outflow_usd=Decimal("5000000.0")))

    # Case 1: Verified massive net outflow ($10M) -> emits BUY proposal
    obs_verified = [
        OnChainObservation(
            chain="ethereum",
            block_number=19000000,
            block_time=now - timedelta(minutes=10),
            available_at=now,
            entity_address="0x123",
            metric_type=OnChainMetricType.EXCHANGE_OUTFLOW,
            metric_value=Decimal("10000000.0"),
            provider="glassnode",
            is_verified_label=True,
            provenance=prov,
        )
    ]
    prop = strat.evaluate("ETH/USDT", current_price=Decimal("3000.0"), observations=obs_verified, as_of=now)
    assert prop is not None
    assert prop.symbol == "ETH/USDT"
    assert prop.side.value == "buy"
    assert prop.entry == 3000.0
    assert prop.stop < 3000.0
    assert prop.target > 3000.0
    assert prop.panel_confidence >= 0.60

    # Case 2: Unverified label -> confidence haircuts below 0.60 -> ABSTAINS (None)
    obs_unverified = [
        OnChainObservation(
            chain="ethereum",
            block_number=19000001,
            block_time=now - timedelta(minutes=5),
            available_at=now,
            entity_address="0x456",
            metric_type=OnChainMetricType.EXCHANGE_OUTFLOW,
            metric_value=Decimal("10000000.0"),
            provider="glassnode",
            is_verified_label=False,  # Unverified wallet!
            provenance=prov,
        )
    ]
    prop2 = strat.evaluate("ETH/USDT", current_price=Decimal("3000.0"), observations=obs_unverified, as_of=now)
    assert prop2 is None
