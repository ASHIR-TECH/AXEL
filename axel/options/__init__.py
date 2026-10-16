"""
Options package for AXEL — Phase 8.

Defined-risk options research and trading under the deterministic core.
All pricing, payoff, and risk logic is pure and deterministic. LLMs propose
OptionsProposals only; they never reach execution without passing the
deterministic validator and risk gate.
"""

from axel.options.contracts import (
    OptionsProposal,
    OptionsRiskDecision,
)
from axel.options.payoff import (
    PayoffGrid,
    compute_break_even_points,
    compute_leg_payoff,
    compute_structure_payoff,
)
from axel.options.payoff import (
    max_gain_for_structure as max_gain,
)
from axel.options.payoff import (
    max_loss_for_structure as max_loss,
)
from axel.options.pricing import (
    Greeks,
    black_scholes_price,
    implied_volatility,
)
from axel.options.risk import (
    OPTIONS_LIMITS,
    OptionsRiskLimits,
    validate_defined_risk,
)
from axel.options.types import (
    ExerciseStyle,
    OptionContract,
    OptionLeg,
    OptionQuote,
    OptionRight,
    OptionSide,
    SettlementType,
)
from axel.options.validator import (
    ACCEPT,
    REJECT,
    REQUIRES_REVIEW,
    ValidationResult,
    validate_options_proposal,
)

__all__ = [
    "ACCEPT",
    "OPTIONS_LIMITS",
    "REJECT",
    "REQUIRES_REVIEW",
    "ExerciseStyle",
    "Greeks",
    "OptionContract",
    "OptionLeg",
    "OptionQuote",
    "OptionRight",
    "OptionSide",
    "OptionsProposal",
    "OptionsRiskDecision",
    "OptionsRiskLimits",
    "PayoffGrid",
    "SettlementType",
    "ValidationResult",
    "black_scholes_price",
    "compute_break_even_points",
    "compute_leg_payoff",
    "compute_structure_payoff",
    "implied_volatility",
    "max_gain",
    "max_loss",
    "validate_defined_risk",
    "validate_options_proposal",
]
