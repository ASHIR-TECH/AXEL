"""
Tests for Phase 7 Crypto Backtester and honest funding / fee accounting.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from axel.crypto.backtest.backtester import CryptoBacktester
from axel.crypto.schemas import FundingRateRecord
from axel.data.schemas import BarRecord, Provenance


def test_crypto_backtester_funding_accrual():
    now = datetime.now(UTC)
    prov = Provenance(source="binance", source_id="bar_1", source_hash="h1", ingestion_timestamp=now)

    # Generate synthetic 1h bars for 48 hours
    bars = []
    base_price = 50000.0
    for i in range(48):
        t = now + timedelta(hours=i)
        bar = BarRecord(
            symbol="BTC/USDT:USDT",
            venue="binance",
            event_time=t,
            available_at=t + timedelta(hours=1),
            open=base_price,
            high=base_price + 100.0,
            low=base_price - 100.0,
            close=base_price + 20.0,
            volume=50.0,
            adjustment_status="raw",
            provenance=prov,
        )
        bars.append(bar)

    # Generate funding records every 8 hours with high rate (0.05% per cycle = longs pay shorts)
    fundings = []
    for i in range(0, 48, 8):
        t = now + timedelta(hours=i)
        f_rec = FundingRateRecord(
            venue="binance",
            symbol="BTC/USDT:USDT",
            event_time=t,
            available_at=t,
            rate=Decimal("0.0005"),  # 0.05%
            provenance=prov,
        )
        fundings.append(f_rec)

    backtester = CryptoBacktester(
        initial_capital=Decimal("100000.0"),
        taker_fee_bps=Decimal("2.0"),
        slippage_bps=Decimal("1.0"),
    )

    result = backtester.run_perpetual_carry_backtest(
        bars=bars,
        funding_records=fundings,
        position_size_usd=Decimal("10000.0"),
    )

    assert result.total_trades > 0
    # Funding was positive and position was short -> collected funding
    assert result.total_funding_pnl > Decimal("0.0")
    assert result.total_fees_paid > Decimal("0.0")
    assert len(result.trades) == result.total_trades
    assert result.avg_expectancy_r != 0.0
