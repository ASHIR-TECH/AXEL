from dataclasses import FrozenInstanceError

import pytest

from axel.risk.limits import LIMITS


def test_risk_limits_immutability():
    # Attempting to alter any limit at runtime must raise FrozenInstanceError
    with pytest.raises(FrozenInstanceError):
        LIMITS.GLOBAL_MAX_DRAWDOWN = 0.20

    with pytest.raises(FrozenInstanceError):
        LIMITS.MAX_POSITION_PCT = 0.50

    with pytest.raises(FrozenInstanceError):
        LIMITS.SECTION_DAILY_STOP = 0.10


def test_risk_limits_default_values():
    assert LIMITS.GLOBAL_MAX_DRAWDOWN == 0.10
    assert LIMITS.SECTION_DAILY_STOP == 0.03
    assert LIMITS.MAX_POSITION_PCT == 0.05
    assert LIMITS.KELLY_CAP == 0.25
    assert LIMITS.MIN_RR_RATIO == 1.5
    assert LIMITS.MAX_EXPECTED_R == 4.0
    assert LIMITS.MIN_PANEL_CONFIDENCE == 0.30
