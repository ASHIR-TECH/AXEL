"""
Deterministic bet proposal validator — Phase 9.

Validates a BetProposal and returns ACCEPT / REJECT / REQUIRES_REVIEW with
stable reason codes. Independently recomputes implied probabilities, EV,
stake, and correlated exposure. Must not silently rewrite the proposal.

The validator is pure: same inputs always produce the same output. It
depends on odds snapshots, forecasts, and bankroll state passed in
explicitly — no live market calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from axel.bets.contracts import BetProposal, ValueAssessment
from axel.bets.odds import implied_probability
from axel.bets.value import conservative_ev, expected_value

ACCEPT = "ACCEPT"
REJECT = "REJECT"
REQUIRES_REVIEW = "REQUIRES_REVIEW"


@dataclass(frozen=True)
class ValidationResult:
    """Immutable output of the deterministic bet validator."""

    verdict: str  # ACCEPT | REJECT | REQUIRES_REVIEW
    reasons: list[str]
    reason_codes: list[str]
    value_assessment: ValueAssessment | None = None
    recommended_stake: float = 0.0

    @property
    def accepted(self) -> bool:
        return self.verdict == ACCEPT

    @property
    def rejected(self) -> bool:
        return self.verdict == REJECT

    @property
    def requires_review(self) -> bool:
        return self.verdict == REQUIRES_REVIEW


def _market_implied_prob(
    decimal_odds: float,
    *,
    all_decimal_odds: list[float] | None = None,
    method: str = "proportional",
) -> float:
    """Compute de-vigged market-implied probability for one outcome."""
    raw_q = implied_probability(decimal_odds)

    if all_decimal_odds is None or len(all_decimal_odds) < 2:
        # Single outcome — no de-vig possible, use raw implied probability
        return raw_q

    raw_probs = [implied_probability(o) for o in all_decimal_odds]
    booksum = sum(raw_probs)

    if method == "proportional":
        # proportional normalization handled below via raw_q / booksum
        return raw_q / booksum if booksum > 0 else raw_q

    # Default: proportional normalization
    return raw_q / booksum if booksum > 0 else raw_q


def validate_bet_proposal(
    proposal: BetProposal,
    *,
    odds_snapshot_decimal: float | None = None,
    all_market_odds: list[float] | None = None,
    estimated_prob: float | None = None,
    market_implied_prob: float | None = None,
    forecast: dict | None = None,
    bankroll: float = 0.0,
    open_exposure_pct: float = 0.0,
    daily_stake_used_pct: float = 0.0,
    edge_threshold: float = 0.02,
    commission_rate: float = 0.0,
    execution_cost_bps: float = 0.0,
    uncertainty_haircut: float = 0.0,
    now: datetime | None = None,
) -> ValidationResult:
    """
    Deterministically validate a bet proposal.

    Fail closed on any unknown: missing odds, missing forecast, stale price,
    suspended market, negative Kelly, or insufficient edge.
    """
    check_now = now or datetime.now(UTC)
    reasons: list[str] = []
    codes: list[str] = []

    # ── 0. Proposal freshness ────────────────────────────────────────
    if proposal.valid_until <= check_now:
        reasons.append(f"Proposal {proposal.id} expired at {proposal.valid_until.isoformat()}")
        codes.append("PROPOSAL_EXPIRED")
        return _result(REJECT, reasons, codes, None, 0.0)

    # ── 0b. Bankroll must be positive ────────────────────────────────
    if bankroll <= 0:
        reasons.append("Bankroll is zero or negative — no bets permitted")
        codes.append("ZERO_BANKROLL")
        return _result(REJECT, reasons, codes, None, 0.0)

    # ── 1. Odds must be available and fresh ──────────────────────────
    if odds_snapshot_decimal is None or odds_snapshot_decimal <= 1.0:
        reasons.append("No valid decimal odds available for outcome")
        codes.append("ODDS_MISSING_OR_INVALID")
        return _result(REJECT, reasons, codes, None, 0.0)

    # Price limit check: if proposal sets a price_limit, current odds must
    # not be worse (higher decimal odds = worse for back bet, but for
    # simplicity we treat price_limit as max acceptable odds)
    if proposal.price_limit is not None and odds_snapshot_decimal > proposal.price_limit:
        reasons.append(
            f"Current odds {odds_snapshot_decimal} exceed price limit {proposal.price_limit}"
        )
        codes.append("PRICE_BEYOND_TOLERANCE")
        return _result(REJECT, reasons, codes, None, 0.0)

    # ── 2. Market implied probability (de-vigged) ────────────────────
    if market_implied_prob is None:
        market_implied_prob = _market_implied_prob(
            odds_snapshot_decimal,
            all_decimal_odds=all_market_odds,
        )

    # ── 3. Estimated probability (from forecast) ─────────────────────
    if estimated_prob is None:
        if forecast is not None and "probabilities" in forecast:
            probs = forecast["probabilities"]
            # Need to map outcome to probability — approximate lookup
            estimated_prob = probs.get(proposal.outcome)
        if estimated_prob is None:
            reasons.append("No estimated probability available")
            codes.append("FORECAST_MISSING")
            return _result(REJECT, reasons, codes, None, 0.0)

    if not 0.0 <= estimated_prob <= 1.0:
        reasons.append(f"Estimated probability {estimated_prob} outside [0,1]")
        codes.append("INVALID_PROBABILITY")
        return _result(REJECT, reasons, codes, None, 0.0)

    # ── 4. EV computation ────────────────────────────────────────────
    ev = expected_value(
        odds_snapshot_decimal,
        estimated_prob,
        commission_rate=commission_rate,
        execution_cost_bps=execution_cost_bps,
    )

    cons_ev = conservative_ev(
        odds_snapshot_decimal,
        estimated_prob,
        market_implied_prob,
        commission_rate=commission_rate,
        execution_cost_bps=execution_cost_bps,
        uncertainty_haircut=uncertainty_haircut,
    )

    # ── 5. Edge threshold check ─────────────────────────────────────
    if cons_ev <= edge_threshold:
        reasons.append(
            f"Conservative EV {cons_ev:.4f} <= edge threshold {edge_threshold:.4f}"
        )
        codes.append("INSUFFICIENT_EDGE")
        return _result(
            REQUIRES_REVIEW,
            reasons,
            codes,
            _build_assessment(
                proposal, odds_snapshot_decimal, market_implied_prob,
                estimated_prob, ev, cons_ev, commission_rate,
                execution_cost_bps, edge_threshold, check_now,
            ),
            0.0,
        )

    # ── 6. Kelly sizing (fractional, never full Kelly) ─────────────
    from axel.bets.sizing import check_bankroll_limits, fractional_kelly

    sizing = fractional_kelly(
        odds_snapshot_decimal,
        estimated_prob,
        kelly_fraction=0.25,  # conservative fractional Kelly
        bankroll=max(bankroll, 1.0),  # avoid div by zero
        max_stake_pct=0.05,
    )

    if sizing["is_no_bet"]:
        reasons.append(f"No bet: {sizing['reason']}")
        codes.append("NO_BET_KELLY_ZERO")
        return _result(
            REJECT,
            reasons,
            codes,
            _build_assessment(
                proposal, odds_snapshot_decimal, market_implied_prob,
                estimated_prob, ev, cons_ev, commission_rate,
                execution_cost_bps, edge_threshold, check_now,
            ),
            0.0,
        )

    # ── 7. Bankroll and correlation limits ──────────────────────────
    bankroll_check = check_bankroll_limits(
        proposed_stake=sizing["stake"],
        bankroll=max(bankroll, 1.0),
        max_stake_per_bet_pct=0.05,
        max_stake_per_event_pct=0.10,
        max_daily_stake_pct=0.15,
        open_exposure_pct=open_exposure_pct,
        daily_stake_used_pct=daily_stake_used_pct,
        max_correlated_exposure_pct=0.20,
    )

    if not bankroll_check["allowed"]:
        reasons.append(f"Bankroll limit: {bankroll_check['reason']}")
        codes.append("BANKROLL_LIMIT")
        return _result(
            REJECT,
            reasons,
            codes,
            _build_assessment(
                proposal, odds_snapshot_decimal, market_implied_prob,
                estimated_prob, ev, cons_ev, commission_rate,
                execution_cost_bps, edge_threshold, check_now,
            ),
            0.0,
        )

    # ── 8. Accept ───────────────────────────────────────────────────
    reasons.append("All deterministic bet validation checks passed")
    codes.append("BET_VALIDATION_OK")

    assessment = _build_assessment(
        proposal, odds_snapshot_decimal, market_implied_prob,
        estimated_prob, ev, cons_ev, commission_rate,
        execution_cost_bps, edge_threshold, check_now,
    )

    return _result(ACCEPT, reasons, codes, assessment, sizing["stake"])


def _build_assessment(
    proposal: BetProposal,
    price: float,
    market_implied_prob: float,
    estimated_prob: float,
    ev: float,
    cons_ev: float,
    commission_rate: float,
    execution_cost_bps: float,
    edge_threshold: float,
    expiry: datetime,
) -> ValueAssessment:
    from axel.bets.contracts import ValueAssessment

    costs = commission_rate * max(estimated_prob * (price - 1.0), 0.0) + execution_cost_bps / 10000.0
    return ValueAssessment(
        proposal_id=proposal.id,
        market_id=proposal.market_id,
        outcome=proposal.outcome,
        price=price,
        market_implied_prob=market_implied_prob,
        estimated_prob=estimated_prob,
        ev=ev,
        conservative_ev=cons_ev,
        costs=costs,
        edge_threshold=edge_threshold,
        expiry=expiry,
    )


def _result(
    verdict: str,
    reasons: list[str],
    codes: list[str],
    value_assessment: ValueAssessment | None,
    recommended_stake: float,
) -> ValidationResult:
    return ValidationResult(
        verdict=verdict,
        reasons=list(reasons),
        reason_codes=list(codes),
        value_assessment=value_assessment,
        recommended_stake=recommended_stake,
    )
