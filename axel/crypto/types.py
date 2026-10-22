"""
Crypto vertical types, enumerations, and constants for AXEL (Phase 7).
"""

from enum import Enum


class CryptoInstrumentType(str, Enum):
    SPOT = "spot"
    PERPETUAL = "perpetual"
    DELIVERY_FUTURE = "delivery_future"


class FundingIntervalHours(int, Enum):
    EIGHT_HOURS = 8
    FOUR_HOURS = 4
    ONE_HOUR = 1


class ArbitrageLegStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    FAILED = "failed"
    HEDGED = "hedged"
    REPAIRED = "repaired"
    CANCELED = "canceled"


class OneLegFailureAction(str, Enum):
    MARKET_HEDGE = "market_hedge"
    CANCEL_AND_UNWIND = "cancel_and_unwind"
    INCIDENT_ESCALATE = "incident_escalate"


class VenueStatus(str, Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    MAINTENANCE = "maintenance"
    HALTED = "halted"


class OnChainMetricType(str, Enum):
    EXCHANGE_INFLOW = "exchange_inflow"
    EXCHANGE_OUTFLOW = "exchange_outflow"
    ACCUMULATION_SCORE = "accumulation_score"
    WHALE_TRANSACTION_COUNT = "whale_transaction_count"
    MVRV_RATIO = "mvrv_ratio"
    ACTIVE_ADDRESSES = "active_addresses"


class CryptoMarketRegime(str, Enum):
    LOW_VOL_RANGE = "low_vol_range"
    HIGH_VOL_TRENDING = "high_vol_trending"
    EXTREME_VOL_DISLOCATION = "extreme_vol_dislocation"
    COMPRESSED_LIQUIDITY = "compressed_liquidity"
