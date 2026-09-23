from axel.data.backtester import summarize_returns
from axel.data.validation import ValidationPolicy, validate_strategy


def test_deliberately_overfit_result_is_rejected() -> None:
    result = summarize_returns([0.05] * 150)
    report = validate_strategy(result, ValidationPolicy(min_sharpe=0.0))
    assert not report.approved
    assert "EXPECTANCY_REQUIRES_LEAKAGE_AUDIT" in report.reasons


def test_insufficient_history_is_rejected() -> None:
    report = validate_strategy(summarize_returns([0.002] * 10), ValidationPolicy(min_sharpe=0.0))
    assert not report.approved
    assert "INSUFFICIENT_OUT_OF_SAMPLE_TRADES" in report.reasons
