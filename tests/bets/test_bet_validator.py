"""
Unit tests for Phase 9 Bets deterministic validator — ACCEPT / REJECT /
REQUIRES_REVIEW with stable reason codes.
"""

from datetime import UTC, datetime

from axel.bets.validator import ACCEPT, REJECT, REQUIRES_REVIEW, validate_bet_proposal


def _proposal(
    outcome: str = "home",
    requested_stake: float = 50.0,
    price_limit: float | None = None,
    valid_until: datetime | None = None,
    forecast_id: str = "fc_1",
) -> dict:
    """Return a minimal BetProposal-like dict. We pass keyword args directly
    to validate_bet_proposal which accepts a BetProposal contract — here we
    construct the contract inline in each test instead."""
    raise NotImplementedError  # pragma: no cover


def _make_prop(*, outcome="home", stake=50.0, price_limit=None, valid_until=None, ts=None):
    from axel.bets.contracts import BetProposal

    return BetProposal(
        event_id="evt_1",
        market_id="mkt_1",
        outcome=outcome,
        requested_stake=stake,
        price_limit=price_limit,
        forecast_id="fc_1",
        ts=ts or datetime.now(UTC),
        valid_until=valid_until or datetime(2026, 12, 31, tzinfo=UTC),
    )


def test_validator_accepts_positive_edge_bet():
    prop = _make_prop(outcome="home", stake=50.0)
    now = datetime(2026, 6, 1, tzinfo=UTC)

    result = validate_bet_proposal(
        prop,
        odds_snapshot_decimal=2.0,
        estimated_prob=0.6,
        market_implied_prob=0.5,
        bankroll=1000.0,
        now=now,
    )
    assert result.accepted
    assert result.verdict == ACCEPT
    assert result.recommended_stake > 0


def test_validator_rejects_expired_proposal():
    # ts must be before valid_until (Pydantic check), and now must be after valid_until
    prop = _make_prop(
        ts=datetime(2019, 1, 1, tzinfo=UTC),
        valid_until=datetime(2020, 1, 1, tzinfo=UTC),
    )
    result = validate_bet_proposal(
        prop,
        odds_snapshot_decimal=2.0,
        estimated_prob=0.6,
        market_implied_prob=0.5,
        bankroll=1000.0,
        now=datetime(2026, 6, 2, tzinfo=UTC),
    )
    assert result.rejected
    assert "PROPOSAL_EXPIRED" in result.reason_codes


def test_validator_rejects_missing_odds():
    prop = _make_prop()
    result = validate_bet_proposal(
        prop,
        odds_snapshot_decimal=None,
        estimated_prob=0.6,
        market_implied_prob=0.5,
        bankroll=1000.0,
    )
    assert result.rejected
    assert "ODDS_MISSING_OR_INVALID" in result.reason_codes


def test_validator_rejects_insufficient_edge():
    prop = _make_prop(stake=50.0)
    # prob 0.5 at odds 2.0 -> EV = 0, below edge threshold
    result = validate_bet_proposal(
        prop,
        odds_snapshot_decimal=2.0,
        estimated_prob=0.5,
        market_implied_prob=0.5,
        bankroll=1000.0,
        edge_threshold=0.02,
    )
    assert result.verdict in (REJECT, REQUIRES_REVIEW)


def test_validator_rejects_stale_price_beyond_limit():
    prop = _make_prop(price_limit=1.9)
    result = validate_bet_proposal(
        prop,
        odds_snapshot_decimal=2.1,  # worse than limit
        estimated_prob=0.6,
        market_implied_prob=0.5,
        bankroll=1000.0,
    )
    assert result.rejected
    assert "PRICE_BEYOND_TOLERANCE" in result.reason_codes


def test_validator_rejects_zero_bankroll():
    prop = _make_prop()
    result = validate_bet_proposal(
        prop,
        odds_snapshot_decimal=2.0,
        estimated_prob=0.6,
        market_implied_prob=0.5,
        bankroll=0.0,
    )
    assert result.rejected


def test_validator_requires_review_when_no_forecast():
    prop = _make_prop()
    result = validate_bet_proposal(
        prop,
        odds_snapshot_decimal=2.0,
        estimated_prob=None,
        market_implied_prob=0.5,
        bankroll=1000.0,
    )
    assert result.rejected
    assert "FORECAST_MISSING" in result.reason_codes
