from axel.core.types import Section
from axel.risk.limits import LIMITS
from axel.risk.loss_manager import DailyPnLState, LossManager


def test_loss_manager_within_limits():
    lm = LossManager(LIMITS)
    state = DailyPnLState(
        section=Section.STOCKS,
        start_of_day_equity=100_000.0,
        realized_pnl=-500.0,
        unrealized_pnl=-1_000.0,  # -1.5% combined
    )
    res = lm.evaluate_daily_stop(state)
    assert not res.is_breached
    assert res.action == "ALLOW"


def test_loss_manager_breach():
    lm = LossManager(LIMITS)
    state = DailyPnLState(
        section=Section.STOCKS,
        start_of_day_equity=100_000.0,
        realized_pnl=-2_000.0,
        unrealized_pnl=-1_500.0,  # -3.5% combined (exceeds -3.0%)
    )
    res = lm.evaluate_daily_stop(state)
    assert res.is_breached
    assert res.action == "BLOCK_NEW_ENTRIES"
    assert "BREACHED" in (res.reason or "")
