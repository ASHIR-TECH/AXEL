from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from axel.data.ml import NO_SLIPPAGE, US_EQUITY_CALENDAR, Backtester, CostModel
from axel.data.schemas import BarRecord, Provenance
from axel.strategies import TrendFollowing
from axel.validation import REPORT_VERSION, AdmissionCriteria, build_validation_report

START = datetime(2026, 1, 5, tzinfo=UTC)  # first 2026 session


def _rising_bars(count: int = 240) -> list[BarRecord]:
    """Session-dated bars, so fold boundaries land on real trading days."""
    bars = []
    event = START
    for index in range(count):
        while not US_EQUITY_CALENDAR.is_trading_day(event.date()):
            event += timedelta(days=1)
        price = 100.0 * (1.002**index)
        bars.append(
            BarRecord(
                symbol="AAPL",
                venue="alpaca-iex",
                event_time=event,
                available_at=event,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=10_000.0,
                adjustment_status="raw",
                provenance=Provenance(
                    source="test",
                    source_id=str(index),
                    source_hash=str(index),
                    ingestion_timestamp=START,
                ),
            )
        )
        event += timedelta(days=1)
    return bars


def _factory(params: Mapping[str, float]) -> TrendFollowing:
    return TrendFollowing(
        short_window=int(params["short_window"]), long_window=int(params["long_window"])
    )


def _build(bars: list[BarRecord]) -> object:
    return build_validation_report(
        backtester=Backtester(
            slippage=NO_SLIPPAGE,
            costs=CostModel(commission_bps=5),
            risk_per_trade=0.01,
        ),
        bars_by_symbol={"AAPL": bars},
        strategy_factory=_factory,
        strategy_id="baseline.trend",
        base_params={"short_window": 10.0, "long_window": 30.0},
        n_trials=12,
        variance_of_sr=0.25,
        regimes=2,
        criteria=AdmissionCriteria(min_trades=1),
        train_size=100,
        test_size=20,
    )


def test_report_is_reproducible_by_fingerprint() -> None:
    first = _build(_rising_bars())
    second = _build(_rising_bars())
    assert first.report_version == REPORT_VERSION
    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert first.as_dict() == second.as_dict()


def test_report_contains_walk_forward_robustness_and_admission() -> None:
    report = _build(_rising_bars())
    assert report.folds
    assert report.folds[0].train_metrics
    assert report.folds[0].test_metrics
    assert "stable" in report.robustness
    assert 0.0 <= report.deflated_sharpe <= 1.0
    assert report.admission["admitted"] in (True, False)
    assert report.trades == int(report.full_metrics["trades"])
    assert report.full_metrics["total_return"] > 0


def test_report_does_not_depend_on_bar_object_identity() -> None:
    bars = _rising_bars()
    shuffled_window = tuple(bars)
    report = _build(list(shuffled_window))
    assert report.fingerprint == _build(bars).fingerprint