"""
Deterministic, point-in-time crypto feature computation engine (Phase 7).
Enforces availability_at >= event_time and pure function transformations.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal

from axel.crypto.schemas import (
    CryptoFeatureRecord,
    CryptoOrderBookRecord,
    FundingRateRecord,
    OnChainObservation,
)
from axel.crypto.types import OnChainMetricType
from axel.data.schemas import BarRecord


class CryptoFeatureEngine:
    """
    Transforms canonical crypto records into versioned feature records.
    Every feature is stamped with availability and can be audited.
    """

    FEATURE_SET_VERSION = "crypto-feat-1"
    TRANSFORMATION_VERSION = "trans-1"

    # ── 1. Returns & Momentum ────────────────────────────────────────────────
    @classmethod
    def compute_return(
        cls,
        bars: Sequence[BarRecord],
        lookback: int = 24,
    ) -> CryptoFeatureRecord:
        if len(bars) < lookback + 1:
            raise ValueError(f"Need at least {lookback + 1} bars, got {len(bars)}")
        target_bar = bars[-1]
        prior_bar = bars[-(lookback + 1)]
        ret = (Decimal(str(target_bar.close)) / Decimal(str(prior_bar.close))) - Decimal("1.0")

        return CryptoFeatureRecord(
            instrument_or_entity=target_bar.symbol,
            event_time=target_bar.event_time,
            available_at=target_bar.available_at,
            venue_or_source=target_bar.venue,
            feature_family="returns",
            feature_name=f"return_{lookback}p",
            value=round(ret, 6),
            feature_set_version=cls.FEATURE_SET_VERSION,
            transformation_version=cls.TRANSFORMATION_VERSION,
        )

    # ── 2. Realized Volatility ───────────────────────────────────────────────
    @classmethod
    def compute_realized_volatility(
        cls,
        bars: Sequence[BarRecord],
        lookback: int = 24,
    ) -> CryptoFeatureRecord:
        if len(bars) < lookback + 1:
            raise ValueError(f"Need at least {lookback + 1} bars, got {len(bars)}")
        target_bar = bars[-1]
        returns = []
        for i in range(len(bars) - lookback, len(bars)):
            p1 = bars[i].close
            p0 = bars[i - 1].close
            returns.append(math.log(p1 / p0))

        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1)
        vol = math.sqrt(variance) * math.sqrt(365 * 24)  # Annualized hourly vol

        return CryptoFeatureRecord(
            instrument_or_entity=target_bar.symbol,
            event_time=target_bar.event_time,
            available_at=target_bar.available_at,
            venue_or_source=target_bar.venue,
            feature_family="volatility",
            feature_name=f"realized_vol_{lookback}p",
            value=Decimal(str(round(vol, 6))),
            feature_set_version=cls.FEATURE_SET_VERSION,
            transformation_version=cls.TRANSFORMATION_VERSION,
        )

    # ── 3. Liquidity, Spread & Depth ─────────────────────────────────────────
    @classmethod
    def compute_liquidity_features(
        cls,
        book: CryptoOrderBookRecord,
    ) -> list[CryptoFeatureRecord]:
        spread_bps = book.spread_bps
        bid_depth_5 = book.bid_depth_usd(5)
        ask_depth_5 = book.ask_depth_usd(5)
        depth_imbalance = (
            (bid_depth_5 - ask_depth_5) / (bid_depth_5 + ask_depth_5)
            if (bid_depth_5 + ask_depth_5) > 0
            else Decimal("0.0")
        )

        return [
            CryptoFeatureRecord(
                instrument_or_entity=book.symbol,
                event_time=book.event_time,
                available_at=book.available_at,
                venue_or_source=book.venue,
                feature_family="liquidity",
                feature_name="spread_bps",
                value=round(spread_bps, 4),
                feature_set_version=cls.FEATURE_SET_VERSION,
            ),
            CryptoFeatureRecord(
                instrument_or_entity=book.symbol,
                event_time=book.event_time,
                available_at=book.available_at,
                venue_or_source=book.venue,
                feature_family="liquidity",
                feature_name="depth_5_imbalance",
                value=round(depth_imbalance, 4),
                feature_set_version=cls.FEATURE_SET_VERSION,
            ),
        ]

    # ── 4. Funding & Basis ───────────────────────────────────────────────────
    @classmethod
    def compute_funding_features(
        cls,
        current_funding: FundingRateRecord,
        historical_funding: Sequence[FundingRateRecord],
    ) -> list[CryptoFeatureRecord]:
        # Basis / mark-index relationship
        basis_bps = Decimal("0.0")
        if current_funding.mark_price and current_funding.index_price and current_funding.index_price > 0:
            basis_bps = (
                (current_funding.mark_price - current_funding.index_price) / current_funding.index_price
            ) * Decimal("10000.0")

        # Rolling funding statistics
        rates = [float(f.rate) for f in historical_funding] if historical_funding else [float(current_funding.rate)]
        mean_rate = sum(rates) / len(rates)
        variance = sum((r - mean_rate) ** 2 for r in rates) / len(rates) if len(rates) > 1 else 0.0
        std_rate = math.sqrt(variance) if variance > 0 else 0.0001
        z_score = (float(current_funding.rate) - mean_rate) / std_rate

        # Funding persistence: consecutive periods with same sign
        curr_sign = 1 if current_funding.rate > 0 else (-1 if current_funding.rate < 0 else 0)
        persistence = 0
        for f in reversed(historical_funding):
            s = 1 if f.rate > 0 else (-1 if f.rate < 0 else 0)
            if s == curr_sign and curr_sign != 0:
                persistence += 1
            else:
                break

        return [
            CryptoFeatureRecord(
                instrument_or_entity=current_funding.symbol,
                event_time=current_funding.event_time,
                available_at=current_funding.available_at,
                venue_or_source=current_funding.venue,
                feature_family="funding",
                feature_name="current_rate",
                value=round(current_funding.rate, 8),
                feature_set_version=cls.FEATURE_SET_VERSION,
            ),
            CryptoFeatureRecord(
                instrument_or_entity=current_funding.symbol,
                event_time=current_funding.event_time,
                available_at=current_funding.available_at,
                venue_or_source=current_funding.venue,
                feature_family="funding",
                feature_name="basis_bps",
                value=round(basis_bps, 4),
                feature_set_version=cls.FEATURE_SET_VERSION,
            ),
            CryptoFeatureRecord(
                instrument_or_entity=current_funding.symbol,
                event_time=current_funding.event_time,
                available_at=current_funding.available_at,
                venue_or_source=current_funding.venue,
                feature_family="funding",
                feature_name="funding_zscore",
                value=Decimal(str(round(z_score, 4))),
                feature_set_version=cls.FEATURE_SET_VERSION,
            ),
            CryptoFeatureRecord(
                instrument_or_entity=current_funding.symbol,
                event_time=current_funding.event_time,
                available_at=current_funding.available_at,
                venue_or_source=current_funding.venue,
                feature_family="funding",
                feature_name="funding_persistence",
                value=Decimal(str(persistence)),
                feature_set_version=cls.FEATURE_SET_VERSION,
            ),
        ]

    # ── 5. Cross-Exchange Spreads ────────────────────────────────────────────
    @classmethod
    def compute_cross_exchange_spread(
        cls,
        book_a: CryptoOrderBookRecord,
        book_b: CryptoOrderBookRecord,
        max_skew_seconds: float = 3.0,
    ) -> CryptoFeatureRecord:
        skew = abs((book_a.event_time - book_b.event_time).total_seconds())
        if skew > max_skew_seconds:
            # Mark as stale / missing due to clock skew
            return CryptoFeatureRecord(
                instrument_or_entity=book_a.symbol,
                event_time=max(book_a.event_time, book_b.event_time),
                available_at=max(book_a.available_at, book_b.available_at),
                venue_or_source=f"{book_a.venue}_{book_b.venue}",
                feature_family="arbitrage",
                feature_name="cross_spread_bps",
                value=Decimal("0.0"),
                missingness_flag=True,
                feature_set_version=cls.FEATURE_SET_VERSION,
            )

        mid_a = book_a.mid_price
        mid_b = book_b.mid_price
        avg_mid = (mid_a + mid_b) / Decimal("2.0")
        spread_bps = ((mid_b - mid_a) / avg_mid) * Decimal("10000.0")

        return CryptoFeatureRecord(
            instrument_or_entity=book_a.symbol,
            event_time=max(book_a.event_time, book_b.event_time),
            available_at=max(book_a.available_at, book_b.available_at),
            venue_or_source=f"{book_a.venue}_{book_b.venue}",
            feature_family="arbitrage",
            feature_name="cross_spread_bps",
            value=round(spread_bps, 4),
            missingness_flag=False,
            feature_set_version=cls.FEATURE_SET_VERSION,
        )

    # ── 6. On-Chain Activity ─────────────────────────────────────────────────
    @classmethod
    def compute_onchain_flow_score(
        cls,
        observations: Sequence[OnChainObservation],
    ) -> CryptoFeatureRecord:
        if not observations:
            raise ValueError("No on-chain observations provided")

        latest = observations[-1]
        inflow = Decimal("0.0")
        outflow = Decimal("0.0")

        for obs in observations:
            if obs.metric_type == OnChainMetricType.EXCHANGE_INFLOW:
                inflow += obs.metric_value
            elif obs.metric_type == OnChainMetricType.EXCHANGE_OUTFLOW:
                outflow += obs.metric_value

        # Net flow: positive means net inflow (bearish pressure), negative means net outflow (bullish accumulation)
        net_flow = inflow - outflow

        return CryptoFeatureRecord(
            instrument_or_entity=latest.entity_address,
            event_time=latest.block_time,
            available_at=latest.available_at,
            venue_or_source=latest.chain,
            feature_family="onchain",
            feature_name="net_exchange_flow",
            value=round(net_flow, 4),
            feature_set_version=cls.FEATURE_SET_VERSION,
        )
