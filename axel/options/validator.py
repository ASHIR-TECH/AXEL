"""
Deterministic options proposal validator — Phase 8.

Validates an OptionsProposal and returns ACCEPT / REJECT / REQUIRES_REVIEW
with stable reason codes. It must not silently rewrite the proposal.

The validator is pure: same inputs always produce the same output. It
depends on a data snapshot (quotes, IV, underlying spot) passed in
explicitly — no live market calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from axel.options.contracts import OptionsRiskDecision  # noqa: F401  (re-exported via __init__)
from axel.options.payoff import build_payoff_grid
from axel.options.pricing import implied_volatility
from axel.options.risk import (
    OPTIONS_LIMITS,
    OptionsRiskLimits,
    validate_defined_risk,
)
from axel.options.types import (
    Greeks,
    OptionQuote,
    OptionsProposal,
)

ACCEPT = "ACCEPT"
REJECT = "REJECT"
REQUIRES_REVIEW = "REQUIRES_REVIEW"


@dataclass(frozen=True)
class ValidationResult:
    """Immutable output of the deterministic options validator."""

    verdict: str  # ACCEPT | REJECT | REQUIRES_REVIEW
    reasons: list[str]
    reason_codes: list[str]
    max_loss: float | None
    max_gain: float | None
    break_even_points: tuple[float, ...]
    margin_buffer_usd: float
    config_hash: str
    greeks: Greeks | None = None

    @property
    def accepted(self) -> bool:
        return self.verdict == ACCEPT

    @property
    def rejected(self) -> bool:
        return self.verdict == REJECT

    @property
    def requires_review(self) -> bool:
        return self.verdict == REQUIRES_REVIEW


def _config_hash(limits: OptionsRiskLimits) -> str:
    """Stable hash of the risk config version used for this validation."""
    import hashlib

    payload = (
        f"{limits.DEFINED_RISK_ONLY}|{limits.NO_NAKED_SHORTS}|"
        f"{limits.MAX_POSITION_PCT}|{limits.MAX_PER_TRADE_LOSS_PCT}|"
        f"{limits.MAX_OPEN_POSITIONS}|{limits.MAX_LEGS_PER_STRUCTURE}|"
        f"{limits.MIN_DTE_DAYS}|{limits.MAX_DTE_DAYS}|"
        f"{limits.MAX_CONCENTRATION_PER_UNDERLYING_PCT}|"
        f"{limits.MAX_CONCENTRATION_PER_EXPIRY_PCT}|"
        f"{limits.CORRELATION_BLOCK_THRESHOLD}|{limits.MIN_RR_RATIO}|"
        f"{limits.MAX_EXPECTED_R}|{limits.MIN_DATA_QUALITY}|"
        f"{limits.MARGIN_BUFFER_PCT}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _quote_for(quotes: dict[str, OptionQuote], contract_id: str) -> OptionQuote | None:
    return quotes.get(contract_id)


def _spot_for(
    quotes: dict[str, OptionQuote],
    underlying_symbol: str,
) -> float | None:
    """Best-effort underlying spot from quotes that carry an underlying link.

    OptionQuote does not carry spot directly; callers should pass a spot
    separately. This helper is a fallback that returns None when unknown,
    causing fail-closed behavior downstream.
    """
    return None


def validate_options_proposal(
    proposal: OptionsProposal,
    *,
    quotes: dict[str, OptionQuote],
    spot: float | None = None,
    risk_free_rate: float = 0.05,
    limits: OptionsRiskLimits = OPTIONS_LIMITS,
    now: datetime | None = None,
) -> ValidationResult:
    """Deterministically validate an options proposal.

    Fail closed on any unknown: missing quote, missing entry price,
    unstaleable data, IV solver failure, or undefined payoff.
    """
    check_now = now or datetime.now(UTC)
    reasons: list[str] = []
    codes: list[str] = []
    config_hash = _config_hash(limits)

    # ── 0. Proposal freshness ────────────────────────────────────────
    if proposal.is_expired(now=check_now):
        reasons.append(f"Proposal {proposal.proposal_id} expired at {proposal.valid_until.isoformat()}")
        codes.append("PROPOSAL_EXPIRED")
        return _result(REJECT, reasons, codes, None, None, (), 0.0, config_hash)

    # ── 1. Defined-risk structural check ─────────────────────────────
    if spot is None:
        # Try to derive spot from quotes; if unavailable, fail closed.
        spot = _spot_for(quotes, proposal.underlying_symbol)

    if spot is None:
        reasons.append("Underlying spot price unknown — cannot price or bound risk")
        codes.append("SPOT_UNKNOWN")
        return _result(REJECT, reasons, codes, None, None, (), 0.0, config_hash)

    passed, risk_reasons, risk_codes = validate_defined_risk(proposal, spot, limits)
    reasons.extend(risk_reasons)
    codes.extend(risk_codes)
    if not passed:
        return _result(REJECT, reasons, codes, None, None, (), 0.0, config_hash)

    # ── 2. Quote quality for every leg ───────────────────────────────
    leg_quotes: list[OptionQuote] = []
    for leg in proposal.legs:
        q = _quote_for(quotes, leg.contract.contract_id)
        if q is None:
            reasons.append(f"No quote for contract {leg.contract.contract_id}")
            codes.append("QUOTE_MISSING")
            return _result(REJECT, reasons, codes, None, None, (), 0.0, config_hash)
        if q.is_crossed:
            reasons.append(f"Crossed quote for {leg.contract.contract_id} (bid >= ask)")
            codes.append("QUOTE_CROSSED")
            return _result(REJECT, reasons, codes, None, None, (), 0.0, config_hash)
        if q.quality != limits.MIN_DATA_QUALITY:
            reasons.append(
                f"Quote quality {q.quality} below required {limits.MIN_DATA_QUALITY} "
                f"for {leg.contract.contract_id}"
            )
            codes.append("QUOTE_QUALITY_LOW")
            return _result(REJECT, reasons, codes, None, None, (), 0.0, config_hash)
        leg_quotes.append(q)

    # ── 3. Payoff grid: max loss / gain / break-evens ────────────────
    try:
        grid = build_payoff_grid(proposal, spot)
    except ValueError as exc:
        reasons.append(f"Payoff grid undefined: {exc}")
        codes.append("PAYOFF_UNDEFINED")
        return _result(REJECT, reasons, codes, None, None, (), 0.0, config_hash)

    max_loss = grid.max_loss
    max_gain = grid.max_gain
    break_even_points = grid.break_even_points

    if max_loss >= 0 and not any(
        leg.side.name.lower() == "short" for leg in proposal.legs
    ):
        # Pure long structure can't lose more than premium paid; that's fine.
        pass

    # ── 4. IV sanity per leg (where tradable quotes exist) ───────────
    greeks_list: list[Greeks] = []
    for leg, quote in zip(proposal.legs, leg_quotes, strict=True):
        mid = quote.mid
        if mid is None or mid <= 0:
            # No tradable mid — not necessarily a reject, but flag review.
            reasons.append(f"No tradable mid for {leg.contract.contract_id}")
            codes.append("NO_TRADABLE_MID")
            return _result(
                REQUIRES_REVIEW,
                reasons,
                codes,
                max_loss,
                max_gain,
                break_even_points,
                abs(max_loss) * limits.MARGIN_BUFFER_PCT,
                config_hash,
            )

        tte_years = max((leg.contract.expiry - check_now).total_seconds() / (365.0 * 86400.0), 1e-6)
        try:
            iv, solver_info = implied_volatility(
                market_price=mid,
                inputs=__import__("axel.options.pricing", fromlist=["PricingInputs"]).PricingInputs(  # type: ignore[arg-type]
                    spot=spot,
                    strike=leg.contract.strike,
                    time_to_expiry_years=tte_years,
                    risk_free_rate=risk_free_rate,
                    volatility=0.35,  # initial guess
                    option_right=leg.contract.right,
                    exercise_style=leg.contract.exercise_style,
                ),
            )
            if not solver_info["converged"]:
                reasons.append(f"IV solver did not converge for {leg.contract.contract_id}")
                codes.append("IV_SOLVER_FAIL")
                return _result(
                    REQUIRES_REVIEW,
                    reasons,
                    codes,
                    max_loss,
                    max_gain,
                    break_even_points,
                    abs(max_loss) * limits.MARGIN_BUFFER_PCT,
                    config_hash,
                )
            # Build Greeks via BS at solved IV
            from axel.options.pricing import PricingInputs, black_scholes_price

            _, g = black_scholes_price(
                PricingInputs(
                    spot=spot,
                    strike=leg.contract.strike,
                    time_to_expiry_years=tte_years,
                    risk_free_rate=risk_free_rate,
                    volatility=iv,
                    option_right=leg.contract.right,
                    exercise_style=leg.contract.exercise_style,
                )
            )
            greeks_list.append(g)
        except ValueError:
            reasons.append(f"IV out of arbitrage bounds for {leg.contract.contract_id}")
            codes.append("IV_OUT_OF_BOUNDS")
            return _result(REJECT, reasons, codes, None, None, (), 0.0, config_hash)

    # ── 5. Margin buffer ─────────────────────────────────────────────
    margin_buffer_usd = abs(max_loss) * limits.MARGIN_BUFFER_PCT

    # ── 6. Accept ────────────────────────────────────────────────────
    reasons.append("All deterministic options validation checks passed")
    codes.append("OPTIONS_VALIDATION_OK")

    aggregate_greeks = None
    if greeks_list:
        n = len(greeks_list)
        aggregate_greeks = Greeks(
            delta=sum(g.delta for g in greeks_list) / n,
            gamma=sum(g.gamma for g in greeks_list) / n,
            vega=sum(g.vega for g in greeks_list) / n,
            theta=sum(g.theta for g in greeks_list) / n,
            rho=sum(g.rho for g in greeks_list) / n,
            iv=sum(g.iv for g in greeks_list) / n,
            model="black_scholes_aggregate",
        )

    return _result(
        ACCEPT,
        reasons,
        codes,
        max_loss,
        max_gain,
        break_even_points,
        margin_buffer_usd,
        config_hash,
        aggregate_greeks,
    )


def _result(
    verdict: str,
    reasons: list[str],
    codes: list[str],
    max_loss: float | None,
    max_gain: float | None,
    break_even_points: tuple[float, ...],
    margin_buffer_usd: float,
    config_hash: str,
    greeks: Greeks | None = None,
) -> ValidationResult:
    return ValidationResult(
        verdict=verdict,
        reasons=list(reasons),
        reason_codes=list(codes),
        max_loss=max_loss,
        max_gain=max_gain,
        break_even_points=tuple(break_even_points),
        margin_buffer_usd=margin_buffer_usd,
        config_hash=config_hash,
        greeks=greeks,
    )
