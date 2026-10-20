"""
Canonical point-in-time data schemas and contracts for Phase 7 (Crypto).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from axel.crypto.types import (
    CryptoInstrumentType,
    OnChainMetricType,
    VenueStatus,
)
from axel.data.schemas import Provenance, check_available, sha256_text

CRYPTO_SCHEMA_VERSION = "crypto-canonical-1"
CRYPTO_NORM_VERSION = "crypto-norm-1"


@dataclass(frozen=True)
class CryptoInstrument:
    """Trading pair / contract definition on a specific venue."""

    symbol: str
    venue: str
    base_asset: str
    quote_asset: str
    instrument_type: CryptoInstrumentType
    min_qty: Decimal
    tick_size: Decimal
    contract_size: Decimal = Decimal("1.0")
    funding_interval_hours: int = 8
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.symbol or not self.venue:
            raise ValueError("symbol and venue are required")
        if self.min_qty <= 0 or self.tick_size <= 0 or self.contract_size <= 0:
            raise ValueError("min_qty, tick_size, and contract_size must be positive")


@dataclass(frozen=True)
class FundingRateRecord:
    """Point-in-time perpetual funding rate observation."""

    venue: str
    symbol: str
    event_time: datetime
    available_at: datetime
    rate: Decimal
    funding_interval_hours: int = 8
    predicted_rate: Decimal | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    provenance: Provenance = field(default_factory=lambda: Provenance(
        source="unknown", source_id="0", source_hash="none", ingestion_timestamp=datetime.now(UTC)
    ))
    record_type: str = field(default="crypto_funding", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.venue or not self.symbol:
            raise ValueError("venue and symbol are required")
        if self.funding_interval_hours <= 0:
            raise ValueError("funding_interval_hours must be positive")

    def key(self) -> str:
        return f"funding:{self.venue}:{self.symbol}:{self.event_time.isoformat()}"


@dataclass(frozen=True)
class CryptoOrderBookRecord:
    """L2 snapshot with bids, asks, depth, and spread."""

    venue: str
    symbol: str
    event_time: datetime
    available_at: datetime
    bids: tuple[tuple[Decimal, Decimal], ...]  # (price, size) descending
    asks: tuple[tuple[Decimal, Decimal], ...]  # (price, size) ascending
    provenance: Provenance
    record_type: str = field(default="crypto_orderbook", init=False)

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)
        if not self.venue or not self.symbol:
            raise ValueError("venue and symbol are required")
        if not self.bids or not self.asks:
            raise ValueError("bids and asks must not be empty")
        best_bid = self.bids[0][0]
        best_ask = self.asks[0][0]
        if best_bid <= 0 or best_ask <= 0:
            raise ValueError("prices must be positive")
        if best_bid > best_ask:
            raise ValueError(f"crossed orderbook: best bid {best_bid} > best ask {best_ask}")

    @property
    def best_bid(self) -> Decimal:
        return self.bids[0][0]

    @property
    def best_ask(self) -> Decimal:
        return self.asks[0][0]

    @property
    def spread(self) -> Decimal:
        return self.best_ask - self.best_bid

    @property
    def mid_price(self) -> Decimal:
        return (self.best_bid + self.best_ask) / Decimal(2)

    @property
    def spread_bps(self) -> Decimal:
        mid = self.mid_price
        if mid <= 0:
            return Decimal(0)
        return (self.spread / mid) * Decimal(10000)

    def bid_depth_usd(self, levels: int = 5) -> Decimal:
        return sum((p * q for p, q in self.bids[:levels]), Decimal(0))

    def ask_depth_usd(self, levels: int = 5) -> Decimal:
        return sum((p * q for p, q in self.asks[:levels]), Decimal(0))

    def key(self) -> str:
        return f"orderbook:{self.venue}:{self.symbol}:{self.event_time.isoformat()}"


@dataclass(frozen=True)
class OnChainObservation:
    """
    Normalized point-in-time on-chain data.
    Rule: Unverified wallet labels are never treated as facts (flagged verified=False).
    """

    chain: str
    block_number: int
    block_time: datetime
    available_at: datetime
    entity_address: str
    metric_type: OnChainMetricType
    metric_value: Decimal
    provider: str
    provenance: Provenance
    entity_label: str | None = None
    is_verified_label: bool = False
    classification_version: str = "v1"
    record_type: str = field(default="crypto_onchain", init=False)

    def __post_init__(self) -> None:
        check_available(self.block_time, self.available_at)
        if not self.chain or not self.entity_address:
            raise ValueError("chain and entity_address are required")
        if self.block_number < 0:
            raise ValueError("block_number cannot be negative")

    def key(self) -> str:
        return f"onchain:{self.chain}:{self.block_number}:{self.entity_address}:{self.metric_type.value}"


@dataclass(frozen=True)
class CryptoFeatureRecord:
    """Versioned crypto feature row obeying the crypto feature contract."""

    instrument_or_entity: str
    event_time: datetime
    available_at: datetime
    venue_or_source: str
    feature_family: str  # returns, volatility, liquidity, funding, arbitrage, flow, onchain, regime
    feature_name: str
    value: Decimal
    feature_set_version: str = "crypto-feat-1"
    raw_source_versions: tuple[str, ...] = ("canonical-1",)
    missingness_flag: bool = False
    transformation_version: str = "trans-1"

    def __post_init__(self) -> None:
        check_available(self.event_time, self.available_at)

    def fingerprint(self) -> str:
        content = (
            f"{self.instrument_or_entity}:{self.feature_name}:{self.venue_or_source}:"
            f"{self.feature_set_version}:{self.transformation_version}:{self.value}"
        )
        return sha256_text(content)


@dataclass(frozen=True)
class CryptoVenueHealth:
    """Real-time health status of a crypto venue."""

    venue: str
    status: VenueStatus
    latency_ms: float
    maintenance_mode: bool
    last_heartbeat: datetime
    stale_data_seconds: float = 0.0
    rejection_rate: float = 0.0
    withdrawal_status_ok: bool = True
    active_incidents: int = 0

    @property
    def is_operational(self) -> bool:
        return (
            self.status == VenueStatus.ACTIVE
            and not self.maintenance_mode
            and self.stale_data_seconds < 10.0
            and self.rejection_rate < 0.10
            and self.active_incidents == 0
        )


@dataclass(frozen=True)
class ArbitrageSpreadOpportunity:
    """Synchronized price or funding spread across two venues."""

    symbol: str
    venue_buy: str
    venue_sell: str
    price_buy: Decimal
    price_sell: Decimal
    timestamp_buy: datetime
    timestamp_sell: datetime
    raw_spread_bps: Decimal
    net_edge_bps: Decimal
    available_liquidity_usd: Decimal
    estimated_fees_bps: Decimal
    estimated_slippage_bps: Decimal
    funding_diff_bps: Decimal = Decimal(0)
    venue_risk_haircut_bps: Decimal = Decimal(0)
    is_profitable: bool = False
    rejection_reason: str | None = None
