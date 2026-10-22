"""
Bets / Prediction Markets package for AXEL — Phase 9.

Probability forecasting, price comparison, calibration, portfolio sizing
and settlement for betting and prediction markets. All numeric fields
(odds, probabilities, EV, stake, exposure) are independently recomputed by
deterministic services. LLMs create proposals/commentary only — they never
place bets or alter risk settings.
"""

from axel.bets.contracts import (
    BetFill,
    BetOrder,
    BetProposal,
    ValueAssessment,
)
from axel.bets.odds import (
    implied_probability,
    overround,
    proportional_normalization,
    shin_probabilities,
)
from axel.bets.settlement import (
    compute_payout,
)
from axel.bets.sizing import (
    flat_stake,
    fractional_kelly,
)
from axel.bets.types import (
    BetIntent,
    Event,
    Forecast,
    Market,
    MarketStatus,
    OddsFormat,
    OddsSnapshot,
    OutcomeType,
    SettlementOutcome,
)
from axel.bets.validator import (
    ACCEPT,
    REJECT,
    REQUIRES_REVIEW,
    ValidationResult,
    validate_bet_proposal,
)
from axel.bets.value import (
    conservative_ev,
    expected_value,
)

__all__ = [
    "ACCEPT",
    "REJECT",
    "REQUIRES_REVIEW",
    "BetFill",
    "BetIntent",
    "BetOrder",
    "BetProposal",
    "Event",
    "Forecast",
    "Market",
    "MarketStatus",
    "OddsFormat",
    "OddsSnapshot",
    "OutcomeType",
    "SettlementOutcome",
    "ValidationResult",
    "ValueAssessment",
    "compute_payout",
    "conservative_ev",
    "expected_value",
    "flat_stake",
    "fractional_kelly",
    "implied_probability",
    "overround",
    "proportional_normalization",
    "shin_probabilities",
    "validate_bet_proposal",
]
