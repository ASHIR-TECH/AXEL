
import pytest

from axel.core.config import settings
from axel.risk.killswitch import KillSwitch


def test_killswitch_drawdown_trigger(tmp_path):
    state_file = tmp_path / "ks_test.json"
    ks = KillSwitch(state_file_path=state_file)

    ks.set_high_water_mark(100_000.0)
    assert not ks.is_halted

    # Drop to $95,000 (-5%) -> should not trip
    halted, dd = ks.check_equity(95_000.0)
    assert not halted
    assert round(dd, 4) == 0.05

    # Drop to $89,000 (-11%) -> must trip
    halted, dd = ks.check_equity(89_000.0)
    assert halted
    assert round(dd, 4) == 0.11
    assert ks.is_halted


def test_killswitch_rearm_validation(tmp_path):
    state_file = tmp_path / "ks_test.json"
    ks = KillSwitch(state_file_path=state_file)
    ks.trip("Manual emergency trip")
    assert ks.is_halted

    # Bad token
    with pytest.raises(PermissionError):
        ks.re_arm("wrong-secret", "Valid justification provided here", 100_000.0)

    operator_token = settings.operator_rearm_secret.get_secret_value()

    # Empty / short reason
    with pytest.raises(ValueError):
        ks.re_arm(operator_token, "too short", 100_000.0)

    # Valid re-arm
    ks.re_arm(
        operator_token=operator_token,
        written_rationale="Incident fully analyzed and approved for restart",
        new_equity_baseline=92_000.0,
    )
    assert not ks.is_halted
    assert ks.high_water_mark == 92_000.0
