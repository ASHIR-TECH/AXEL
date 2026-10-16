"""
Options-specific deterministic risk limits and validation — Phase 8.

These are versioned configuration, NOT universal constants hardcoded into
code. Any change requires authorization, audit, and regression tests.
All limits are frozen dataclasses so they cannot be mutated at runtime.
"""

from dataclasses import dataclass

from axel.options.payoff import max_loss_for_structure
from axel.options.types import OptionsProposal


@dataclass(frozen=True)
class OptionsRiskLimits:
    """Deterministic risk thresholds for the Options section."""

    # Structural
    DEFINED_RISK_ONLY: bool = True  # reject any structure with unbounded loss
    NO_NAKED_SHORTS: bool = True  # short options must be covered/spread
    ALLOWED_STRATEGIES: tuple[str, ...] = (
        "long_call",
        "long_put",
        "debit_call_spread",
        "debit_put_spread",
        "credit_call_spread",
        "credit_put_spread",
        "iron_condor",
        "iron_butterfly",
        "calendar_spread",
        "covered_call",
        "cash_secured_put",
    )

    # Position limits
    MAX_POSITION_PCT: float = 0.05  # max notional per trade (of section NAV)
    MAX_PER_TRADE_LOSS_PCT: float = 0.01  # max capital at risk per trade
    MAX_OPEN_POSITIONS: int = 10
    MAX_LEGS_PER_STRUCTURE: int = 4
    MAX_DTE_DAYS: int = 60  # max days-to-expiry for new entries
    MIN_DTE_DAYS: int = 1  # avoid expiry-day lottery tickets

    # Concentration
    MAX_CONCENTRATION_PER_UNDERLYING_PCT: float = 0.20  # 20% of section NAV per underlying
    MAX_CONCENTRATION_PER_EXPIRY_PCT: float = 0.30  # 30% per expiry date
    CORRELATION_BLOCK_THRESHOLD: float = 0.85

    # Risk controls
    MIN_RR_RATIO: float = 1.0  # defined-risk structures can be <1.5R (bounded loss)
    MAX_EXPECTED_R: float = 4.0
    MIN_DATA_QUALITY: str = "VALID"  # quotes below this quality veto

    # Margin
    MARGIN_BUFFER_PCT: float = 0.10  # 10% buffer over calculated max loss


OPTIONS_LIMITS = OptionsRiskLimits()


def is_defined_risk_structure(proposal: OptionsProposal) -> bool:
    """True if every leg's loss is bounded and max_loss is calculable."""
    if not proposal.is_defined_risk:
        return False
    # No naked shorts: any SHORT leg must be part of a spread with a
    # corresponding LONG leg further out (defined risk). This is a heuristic
    # check; the full payoff grid determines boundedness.
    shorts = [leg for leg in proposal.legs if leg.side.name.lower() == "short"]
    longs = [leg for leg in proposal.legs if leg.side.name.lower() == "long"]
    # All-short structure: only defined risk if every short is covered
    # by an underlying position (not tracked here) — fail closed.
    return not (shorts and not longs)


def validate_defined_risk(
    proposal: OptionsProposal,
    spot: float,
    limits: OptionsRiskLimits = OPTIONS_LIMITS,
) -> tuple[bool, list[str], list[str]]:
    """Deterministic defined-risk check.

    Returns (passed, reasons, reason_codes).

    Checks:
    1. Structure is defined-risk (bounded max_loss calculable)
    2. No naked shorts
    3. Leg count within cap
    4. DTE within [MIN_DTE, MAX_DTE]
    5. Max loss within per-trade risk cap (requires section NAV from caller;
       here we only check that max_loss is set and non-negative)
    """
    reasons: list[str] = []
    codes: list[str] = []

    # 1. Leg count
    if len(proposal.legs) == 0:
        reasons.append("No legs in structure")
        codes.append("EMPTY_STRUCTURE")
        return False, reasons, codes

    if len(proposal.legs) > limits.MAX_LEGS_PER_STRUCTURE:
        reasons.append(
            f"{len(proposal.legs)} legs exceeds max {limits.MAX_LEGS_PER_STRUCTURE}"
        )
        codes.append("TOO_MANY_LEGS")
        return False, reasons, codes

    # 2. Defined risk
    if not is_defined_risk_structure(proposal):
        reasons.append("Structure is not defined-risk (unbounded loss or naked short)")
        codes.append("UNDEFINED_RISK")
        return False, reasons, codes

    # 3. max_loss / max_gain set
    if proposal.max_loss is None or proposal.max_loss < 0:
        reasons.append("max_loss not set or negative — cannot bound risk")
        codes.append("MAX_LOSS_UNSET")
        return False, reasons, codes
    if proposal.max_gain is None:
        reasons.append("max_gain not set")
        codes.append("MAX_GAIN_UNSET")
        return False, reasons, codes

    # 4. Payoff grid consistency: max_loss from grid should match proposed
    try:
        grid_max_loss = max_loss_for_structure(proposal, spot)
    except ValueError:
        reasons.append("Payoff grid could not be computed (missing entry price)")
        codes.append("PAYOFF_UNDEFINED")
        return False, reasons, codes

    if abs(grid_max_loss - proposal.max_loss) > 1e-6 * max(1.0, abs(proposal.max_loss)):
        reasons.append(
            f"Proposed max_loss {proposal.max_loss:.4f} inconsistent with "
            f"payoff grid {grid_max_loss:.4f}"
        )
        codes.append("MAX_LOSS_MISMATCH")
        return False, reasons, codes

    # 5. DTE checks on all legs
    now_utc = __import__("datetime").datetime.now(__import__("datetime").UTC)
    for leg in proposal.legs:
        contract = leg.contract
        tte = (contract.expiry - now_utc).total_seconds() / 86400.0
        if tte < limits.MIN_DTE_DAYS:
            reasons.append(
                f"{contract.contract_id}: DTE {tte:.1f}d < min {limits.MIN_DTE_DAYS}d"
            )
            codes.append("DTE_TOO_SHORT")
            return False, reasons, codes
        if tte > limits.MAX_DTE_DAYS:
            reasons.append(
                f"{contract.contract_id}: DTE {tte:.1f}d > max {limits.MAX_DTE_DAYS}d"
            )
            codes.append("DTE_TOO_LONG")
            return False, reasons, codes

    reasons.append("All defined-risk checks passed")
    codes.append("DEFINED_RISK_OK")
    return True, reasons, codes
