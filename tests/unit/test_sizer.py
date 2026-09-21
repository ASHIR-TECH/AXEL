from axel.risk.limits import LIMITS
from axel.risk.sizer import KellySizer


def test_sizer_negative_edge():
    sizer = KellySizer(LIMITS)
    # Win rate 0.35 with 1.0 RR -> negative expectancy
    res = sizer.compute_size(
        nav=100_000.0,
        entry_price=100.0,
        stop_loss=95.0,
        win_rate=0.35,
        avg_win_loss_ratio=1.0,
    )
    assert res.approved_qty == 0.0
    assert res.binding_limit == "ZERO_OR_NEGATIVE_EDGE"
    assert res.notional_usd == 0.0


def test_sizer_notional_cap_binding():
    sizer = KellySizer(LIMITS)
    # Very high win rate (0.85) and high RR (2.5) -> Kelly would suggest large allocation
    # but 5% cap should clamp it to $5,000 max
    res = sizer.compute_size(
        nav=100_000.0,
        entry_price=50.0,
        stop_loss=49.0,  # tight stop
        win_rate=0.85,
        avg_win_loss_ratio=2.5,
    )
    assert res.binding_limit == "NOTIONAL_CAP_5PCT"
    assert res.notional_usd <= 5_000.0
    assert res.approved_qty == 100.0  # 5,000 / 50.0


def test_sizer_risk_at_stop_binding():
    sizer = KellySizer(LIMITS)
    # Wide stop: Entry 100, Stop 80 (20% stop distance)
    # 1% max risk on 100k is $1,000.
    # $1,000 / 0.20 = $5,000 notional.
    # If stop was even wider: Entry 100, Stop 70 (30% stop distance)
    # $1,000 / 0.30 = $3,333 notional -> binding below 5k cap!
    res = sizer.compute_size(
        nav=100_000.0,
        entry_price=100.0,
        stop_loss=70.0,
        win_rate=0.80,
        avg_win_loss_ratio=2.0,
    )
    assert res.binding_limit == "RISK_AT_STOP_CAP"
    assert res.notional_usd <= 3_334.0
    assert res.max_loss_at_stop_usd <= 1_000.0


def test_sizer_fractional_shares_crypto():
    sizer = KellySizer(LIMITS)
    res = sizer.compute_size(
        nav=10_000.0,
        entry_price=3500.0,  # ETH
        stop_loss=3400.0,
        win_rate=0.60,
        avg_win_loss_ratio=2.0,
        asset_type="crypto",
    )
    assert res.approved_qty > 0.0
    # Floating point precision for crypto
    assert isinstance(res.approved_qty, float)
