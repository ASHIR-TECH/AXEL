"""
Reproducible validation report (P2-REP).

Ties the whole Phase 2 chain together: backtest -> walk-forward folds ->
parameter robustness -> deflated Sharpe -> admission decision. The report is a
pure function of its inputs, so identical inputs reproduce an identical
fingerprint. It carries no timestamp by design.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256

from axel.data.ml import Backtester, TradingCalendar
from axel.data.schemas import BarRecord
from axel.validation.admission import (
    AdmissionCriteria,
    evaluate_admission,
    needs_leakage_audit,
)
from axel.validation.deflated_sharpe import deflated_sharpe_ratio
from axel.validation.robustness import RobustnessReport, parameter_variants, robustness_report
from axel.validation.walk_forward import Fold, walk_forward_folds

REPORT_VERSION = "p2-report-1"


@dataclass(frozen=True)
class FoldResult:
    index: int
    train_metrics: Mapping[str, float]
    test_metrics: Mapping[str, float]

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "train_metrics": dict(self.train_metrics),
            "test_metrics": dict(self.test_metrics),
        }


@dataclass(frozen=True)
class ValidationReport:
    report_version: str
    strategy_id: str
    fingerprint: str
    params: Mapping[str, float]
    full_metrics: Mapping[str, float]
    folds: tuple[FoldResult, ...]
    robustness: Mapping[str, object]
    deflated_sharpe: float
    leakage_audit: bool
    admission: Mapping[str, object]
    trades: int

    def as_dict(self) -> dict[str, object]:
        return {
            "report_version": self.report_version,
            "strategy_id": self.strategy_id,
            "fingerprint": self.fingerprint,
            "params": dict(self.params),
            "full_metrics": dict(self.full_metrics),
            "folds": [
                {
                    "index": fold.index,
                    "train_metrics": dict(fold.train_metrics),
                    "test_metrics": dict(fold.test_metrics),
                }
                for fold in self.folds
            ],
            "robustness": dict(self.robustness),
            "deflated_sharpe": self.deflated_sharpe,
            "leakage_audit": self.leakage_audit,
            "admission": dict(self.admission),
            "trades": self.trades,
        }


def _slice(
    bars_by_symbol: Mapping[str, Sequence[BarRecord]], window: slice
) -> dict[str, tuple[BarRecord, ...]]:
    return {symbol: tuple(bars[window]) for symbol, bars in bars_by_symbol.items()}


def run_fold(
    backtester: Backtester,
    bars_by_symbol: Mapping[str, Sequence[BarRecord]],
    strategy: object,
    fold: Fold,
    index: int,
) -> FoldResult:
    """
    Train and test each fold on its own slice.

    Slices are positional, and bar positions correspond to sessions, so the
    boundaries must land on trading days or a fold could begin on a Saturday and
    annualise as if fewer sessions had elapsed than it claims. Sliding to the
    next session keeps elapsed-time annualisation honest inside every fold.
    """
    calendar = backtester.calendar
    train_window, test_window = fold.train_slice, fold.test_slice
    if calendar is not None:
        train_window = _align_to_session(calendar, bars_by_symbol, train_window, "start")
        train_window = _align_to_session(calendar, bars_by_symbol, train_window, "end")
        test_window = _align_to_session(calendar, bars_by_symbol, test_window, "start")
        test_window = _align_to_session(calendar, bars_by_symbol, test_window, "end")
    train = backtester.run(_slice(bars_by_symbol, train_window), strategy)
    test = backtester.run(_slice(bars_by_symbol, test_window), strategy)
    return FoldResult(index=index, train_metrics=train.metrics(), test_metrics=test.metrics())


def _align_to_session(
    calendar: TradingCalendar,
    bars_by_symbol: Mapping[str, Sequence[BarRecord]],
    window: slice,
    edge: str,
) -> slice:
    """Nudge a fold boundary onto a trading session without inverting it."""
    bars = next((series for series in bars_by_symbol.values() if series), ())
    if not bars:
        return window
    start, stop = window.start or 0, window.stop or len(bars)
    if edge == "start":
        while start < len(bars) and not calendar.is_trading_day(bars[start].event_time.date()):
            start += 1
    else:
        stop = min(stop, len(bars))
        while stop > 0 and not calendar.is_trading_day(bars[stop - 1].event_time.date()):
            stop -= 1
    if stop <= start:
        return window
    return slice(start, stop)


def build_validation_report(
    *,
    backtester: Backtester,
    bars_by_symbol: Mapping[str, Sequence[BarRecord]],
    strategy_factory: Callable[[Mapping[str, float]], object],
    strategy_id: str,
    base_params: Mapping[str, float],
    n_trials: int = 1,
    variance_of_sr: float = 0.0,
    regimes: int = 1,
    criteria: AdmissionCriteria | None = None,
    perturbation_pct: float = 0.2,
    train_size: int = 60,
    test_size: int = 20,
) -> ValidationReport:
    strategy = strategy_factory(base_params)
    full = backtester.run(bars_by_symbol, strategy)
    full_metrics = full.metrics()
    n_periods = min((len(bars) for bars in bars_by_symbol.values()), default=0)
    folds = walk_forward_folds(
        n_periods, train_size=train_size, test_size=test_size
    )
    fold_results = tuple(
        run_fold(backtester, bars_by_symbol, strategy, fold, position)
        for position, fold in enumerate(folds)
    )

    variant_expectancies = [
        backtester.run(bars_by_symbol, strategy_factory(variant)).metrics()["expectancy_r"]
        for variant in parameter_variants(base_params, pct=perturbation_pct)
    ]
    robustness: RobustnessReport = robustness_report(
        full_metrics["expectancy_r"], variant_expectancies
    )

    trial_count = max(n_trials, len(variant_expectancies) + 1)
    deflated = deflated_sharpe_ratio(
        full_metrics["sharpe"],
        n_trials=trial_count,
        variance_of_sr=variance_of_sr,
        n_returns=max(len(full.equity_curve) - 1, 2),
    )
    decision = evaluate_admission(
        full_metrics, deflated_sharpe=deflated, regimes=regimes, criteria=criteria
    )

    payload = {
        "strategy_id": strategy_id,
        "params": dict(base_params),
        "metrics": dict(full_metrics),
        "folds": [fold.as_dict() for fold in fold_results],
        "robustness": robustness.as_dict(),
        "deflated_sharpe": deflated,
        "admission": decision.as_dict(),
    }
    fingerprint = sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    return ValidationReport(
        report_version=REPORT_VERSION,
        strategy_id=strategy_id,
        fingerprint=fingerprint,
        params=dict(base_params),
        full_metrics=dict(full_metrics),
        folds=fold_results,
        robustness=robustness.as_dict(),
        deflated_sharpe=deflated,
        leakage_audit=needs_leakage_audit(full_metrics["expectancy_r"]),
        admission=decision.as_dict(),
        trades=len(full.trades),
    )