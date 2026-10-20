"""
Tests for Phase 7 Crypto data schemas, point-in-time invariants, and on-chain handling.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from axel.crypto.schemas import (
    CryptoFeatureRecord,
    CryptoInstrument,
    CryptoOrderBookRecord,
    FundingRateRecord,
    OnChainObservation,
)
from axel.crypto.types import (
    CryptoInstrumentType,
    OnChainMetricType,
)
from axel.data.schemas import Provenance


def test_crypto_instrument_validation():
    inst = CryptoInstrument(
        symbol="BTC/USDT",
        venue="binance",
        base_asset="BTC",
        quote_asset="USDT",
        instrument_type=CryptoInstrumentType.SPOT,
        min_qty=Decimal("0.001"),
        tick_size=Decimal("0.10"),
    )
    assert inst.symbol == "BTC/USDT"
    assert inst.is_active

    with pytest.raises(ValueError, match="positive"):
        CryptoInstrument(
            symbol="BTC/USDT",
            venue="binance",
            base_asset="BTC",
            quote_asset="USDT",
            instrument_type=CryptoInstrumentType.SPOT,
            min_qty=Decimal("-1.0"),
            tick_size=Decimal("0.10"),
        )


def test_funding_rate_point_in_time_guarantee():
    now = datetime.now(UTC)
    prov = Provenance(
        source="bybit",
        source_id="test_funding",
        source_hash="abc123hash",
        ingestion_timestamp=now,
    )

    # Valid: available_at >= event_time
    record = FundingRateRecord(
        venue="bybit",
        symbol="BTC/USDT:USDT",
        event_time=now,
        available_at=now,
        rate=Decimal("0.0001"),
        provenance=prov,
    )
    assert record.rate == Decimal("0.0001")

    # Invalid: lookahead (available_at < event_time)
    with pytest.raises(ValueError, match="look-ahead"):
        FundingRateRecord(
            venue="bybit",
            symbol="BTC/USDT:USDT",
            event_time=now,
            available_at=now - timedelta(seconds=1),
            rate=Decimal("0.0001"),
            provenance=prov,
        )


def test_crypto_orderbook_metrics_and_spread():
    now = datetime.now(UTC)
    prov = Provenance(source="binance", source_id="book_1", source_hash="hash_1", ingestion_timestamp=now)

    book = CryptoOrderBookRecord(
        venue="binance",
        symbol="BTC/USDT",
        event_time=now,
        available_at=now,
        bids=((Decimal("50000.0"), Decimal("1.5")), (Decimal("49990.0"), Decimal("2.0"))),
        asks=((Decimal("50010.0"), Decimal("1.0")), (Decimal("50020.0"), Decimal("3.0"))),
        provenance=prov,
    )
    assert book.best_bid == Decimal("50000.0")
    assert book.best_ask == Decimal("50010.0")
    assert book.spread == Decimal("10.0")
    assert book.mid_price == Decimal("50005.0")
    assert book.spread_bps > Decimal("1.9")
    assert book.bid_depth_usd(2) == Decimal("50000.0") * Decimal("1.5") + Decimal("49990.0") * Decimal("2.0")

    # Crossed orderbook raises ValueError
    with pytest.raises(ValueError, match="crossed orderbook"):
        CryptoOrderBookRecord(
            venue="binance",
            symbol="BTC/USDT",
            event_time=now,
            available_at=now,
            bids=((Decimal("50050.0"), Decimal("1.0")),),
            asks=((Decimal("50010.0"), Decimal("1.0")),),
            provenance=prov,
        )


def test_onchain_unverified_labels_flagged():
    now = datetime.now(UTC)
    prov = Provenance(source="etherscan", source_id="blk_1", source_hash="hash_blk", ingestion_timestamp=now)

    # Observation with unverified entity label
    obs = OnChainObservation(
        chain="ethereum",
        block_number=19000000,
        block_time=now - timedelta(minutes=5),
        available_at=now,
        entity_address="0x1234567890abcdef",
        entity_label="Suspected Exchange Hot Wallet",
        is_verified_label=False,
        metric_type=OnChainMetricType.EXCHANGE_INFLOW,
        metric_value=Decimal("1500.0"),
        provider="glassnode",
        provenance=prov,
    )
    assert not obs.is_verified_label
    assert obs.metric_value == Decimal("1500.0")

    # Lookahead check for on-chain block time
    with pytest.raises(ValueError, match="look-ahead"):
        OnChainObservation(
            chain="ethereum",
            block_number=19000001,
            block_time=now,
            available_at=now - timedelta(seconds=10),
            entity_address="0xabc",
            metric_type=OnChainMetricType.EXCHANGE_OUTFLOW,
            metric_value=Decimal("500.0"),
            provider="glassnode",
            provenance=prov,
        )


def test_crypto_feature_fingerprinting():
    now = datetime.now(UTC)
    feat1 = CryptoFeatureRecord(
        instrument_or_entity="BTC/USDT",
        event_time=now,
        available_at=now,
        venue_or_source="binance",
        feature_family="funding",
        feature_name="rate_zscore",
        value=Decimal("2.15"),
    )
    feat2 = CryptoFeatureRecord(
        instrument_or_entity="BTC/USDT",
        event_time=now,
        available_at=now,
        venue_or_source="binance",
        feature_family="funding",
        feature_name="rate_zscore",
        value=Decimal("2.15"),
    )
    assert feat1.fingerprint() == feat2.fingerprint()
    assert len(feat1.fingerprint()) == 64
