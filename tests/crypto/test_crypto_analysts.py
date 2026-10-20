"""
Tests for Phase 7 Crypto Analysts and Section Manager:
- Funding Analyst
- On-Chain Analyst
- Market Microstructure Analyst
- Regime & Risk Analyst
- Crypto Section Manager
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from axel.core.contracts import Signal
from axel.core.types import Direction, Section
from axel.crypto.analysts.funding import FundingAnalyst
from axel.crypto.analysts.manager import CryptoSectionManager
from axel.crypto.analysts.microstructure import MarketMicrostructureAnalyst
from axel.crypto.analysts.on_chain import OnChainAnalyst
from axel.crypto.analysts.regime import RegimeRiskAnalyst
from axel.crypto.risk.venue_risk import VenueRiskManager
from axel.crypto.schemas import (
    CryptoOrderBookRecord,
    FundingRateRecord,
    OnChainObservation,
)
from axel.crypto.types import OnChainMetricType
from axel.data.schemas import Provenance


def test_crypto_analysts_signal_generation():
    now = datetime.now(UTC)
    prov = Provenance(source="test", source_id="1", source_hash="h1", ingestion_timestamp=now)

    # 1. Funding Analyst
    funding_analyst = FundingAnalyst(high_funding_threshold=Decimal("0.0003"))
    f_rec = FundingRateRecord(
        venue="binance",
        symbol="BTC/USDT:USDT",
        event_time=now,
        available_at=now,
        rate=Decimal("0.0006"),  # High positive funding: longs pay shorts
        provenance=prov,
    )
    sig_funding = funding_analyst.analyze(f_rec, as_of=now)
    assert sig_funding.section == Section.CRYPTO
    assert sig_funding.direction == Direction.SHORT
    assert sig_funding.strength == 1.0

    # 2. On-Chain Analyst
    onchain_analyst = OnChainAnalyst()
    obs = [
        OnChainObservation(
            chain="ethereum",
            block_number=19000000,
            block_time=now,
            available_at=now,
            entity_address="0xabc",
            metric_type=OnChainMetricType.EXCHANGE_OUTFLOW,
            metric_value=Decimal("5000.0"),
            provider="glassnode",
            is_verified_label=True,
            provenance=prov,
        )
    ]
    sig_onchain = onchain_analyst.analyze("ETH/USDT", obs, as_of=now)
    assert sig_onchain.section == Section.CRYPTO
    assert sig_onchain.direction == Direction.LONG  # Net outflow = accumulation

    # 3. Microstructure Analyst
    micro_analyst = MarketMicrostructureAnalyst()
    book = CryptoOrderBookRecord(
        venue="binance",
        symbol="BTC/USDT",
        event_time=now,
        available_at=now,
        bids=((Decimal("50000.0"), Decimal("10.0")),),  # $500k bid depth
        asks=((Decimal("50010.0"), Decimal("1.0")),),   # $50k ask depth
        provenance=prov,
    )
    sig_micro = micro_analyst.analyze(book, as_of=now)
    assert sig_micro.section == Section.CRYPTO
    assert sig_micro.direction == Direction.LONG
    assert sig_micro.strength > 0.80

    # 4. Regime Analyst
    regime_analyst = RegimeRiskAnalyst()
    sig_regime_dislocation = regime_analyst.analyze("BTC/USDT", realized_vol=1.40, spread_bps=60.0, as_of=now)
    assert sig_regime_dislocation.strength == 0.0  # Caution in extreme dislocation


def test_crypto_section_manager_constraints():
    now = datetime.now(UTC)
    venue_mgr = VenueRiskManager()
    section_manager = CryptoSectionManager(venue_risk_manager=venue_mgr, min_consensus_strength=0.30)

    # 1. Unapproved strategy raises PermissionError
    with pytest.raises(PermissionError, match="not an approved Crypto strategy"):
        section_manager.evaluate_proposal(
            strategy_id="unapproved:degen_strategy",
            symbol="BTC/USDT",
            venue="binance",
            entry=50000.0,
            stop=49000.0,
            target=52000.0,
            signals=[],
        )

    # 2. Disabled venue -> Abstains (returns None)
    venue_mgr.disable_venue("binance", "API latency")
    signals = [
        Signal(
            section=Section.CRYPTO,
            symbol="BTC/USDT",
            analyst="crypto_funding_analyst",
            direction=Direction.LONG,
            strength=0.85,
            as_of=now,
        )
    ]
    prop_disabled = section_manager.evaluate_proposal(
        strategy_id="crypto:funding_arbitrage",
        symbol="BTC/USDT",
        venue="binance",
        entry=50000.0,
        stop=49000.0,
        target=52000.0,
        signals=signals,
    )
    assert prop_disabled is None

    # 3. Enabled venue and strong consensus -> Emits Proposal
    venue_mgr.enable_venue("binance")
    prop_ok = section_manager.evaluate_proposal(
        strategy_id="crypto:funding_arbitrage",
        symbol="BTC/USDT",
        venue="binance",
        entry=50000.0,
        stop=49000.0,
        target=52000.0,
        signals=signals,
    )
    assert prop_ok is not None
    assert prop_ok.section == Section.CRYPTO
    assert prop_ok.symbol == "BTC/USDT"
    assert prop_ok.panel_confidence >= 0.80

    # 4. Conflicting / weak signals -> Abstains (returns None)
    weak_signals = [
        Signal(section=Section.CRYPTO, symbol="BTC/USDT", analyst="a1", direction=Direction.LONG, strength=0.5, as_of=now),
        Signal(section=Section.CRYPTO, symbol="BTC/USDT", analyst="a2", direction=Direction.SHORT, strength=0.5, as_of=now),
    ]
    prop_weak = section_manager.evaluate_proposal(
        strategy_id="crypto:funding_arbitrage",
        symbol="BTC/USDT",
        venue="binance",
        entry=50000.0,
        stop=49000.0,
        target=52000.0,
        signals=weak_signals,
    )
    assert prop_weak is None
