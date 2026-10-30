"""
Fail-closed data-quality and point-in-time checks.

Quality is not a boolean attached after the fact. These helpers inspect the
canonical records before they enter features, backtests or live decisions and
return an explicit report. Missing, stale, duplicated, gapped or future data is
reported as an error; callers that need to proceed must do so deliberately.

The module is asset-agnostic and has no provider/network imports.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from axel.data.events import EventEnvelope
from axel.data.marketdata import (
    FundingRateObservation,
    OpenInterestObservation,
    OrderBookSnapshot,
    QuoteTick,
    TradeSide,
    TradeTick,
)
from axel.data.schemas import BarRecord, require_tz

QUALITY_REPORT_VERSION = "quality-1"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class QualityCode(StrEnum):
    MISSING_DATA = "missing_data"
    MISSING_FIELD = "missing_field"
    NAIVE_TIMESTAMP = "naive_timestamp"
    TIMESTAMP_ORDER = "timestamp_order"
    FUTURE_DATA = "future_data"
    LOOKAHEAD = "lookahead"
    STALE_DATA = "stale_data"
    DUPLICATE_RECORD = "duplicate_record"
    OUT_OF_ORDER = "out_of_order"
    DATA_GAP = "data_gap"
    INVALID_OHLC = "invalid_ohlc"
    NEGATIVE_VOLUME = "negative_volume"
    CROSSED_BOOK = "crossed_book"
    EMPTY_BOOK = "empty_book"
    SEQUENCE_GAP = "sequence_gap"
    TIMESTAMP_DRIFT = "timestamp_drift"
    UNKNOWN_TRADE_SIDE = "unknown_trade_side"
    INSTRUMENT_MISMATCH = "instrument_mismatch"
    SCHEMA_MISMATCH = "schema_mismatch"


@dataclass(frozen=True, slots=True)
class QualityIssue:
    code: QualityCode
    severity: Severity
    message: str
    record_key: str = ""
    details: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "record_key": self.record_key,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Accumulated quality findings for one validation pass."""

    issues: tuple[QualityIssue, ...] = ()
    checked: int = 0
    version: str = QUALITY_REPORT_VERSION

    @property
    def errors(self) -> tuple[QualityIssue, ...]:
        return tuple(
            issue
            for issue in self.issues
            if issue.severity in (Severity.ERROR, Severity.CRITICAL)
        )

    @property
    def warnings(self) -> tuple[QualityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_invalid(self) -> None:
        if not self.ok:
            raise DataQualityError(self)

    def merge(self, other: QualityReport) -> QualityReport:
        return QualityReport(
            issues=self.issues + other.issues,
            checked=self.checked + other.checked,
            version=self.version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "version": self.version,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class DataQualityError(ValueError):
    """Raised when a fail-closed consumer receives an invalid dataset."""

    def __init__(self, report: QualityReport) -> None:
        self.report = report
        summary = "; ".join(issue.message for issue in report.errors[:3])
        super().__init__(f"data quality check failed: {summary}")


@dataclass(frozen=True, slots=True)
class DataQualityPolicy:
    """Thresholds are explicit so a caller cannot silently relax them."""

    max_age_seconds: float | None = None
    max_future_skew_seconds: float = 0.0
    max_gap_multiplier: float = 1.5
    max_sequence_gap: int = 1
    require_monotonic: bool = True
    stale_is_error: bool = True
    unknown_trade_side_is_error: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("max_age_seconds", self.max_age_seconds),
            ("max_future_skew_seconds", self.max_future_skew_seconds),
            ("max_gap_multiplier", self.max_gap_multiplier),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.max_sequence_gap < 0:
            raise ValueError("max_sequence_gap cannot be negative")


def _issue(
    code: QualityCode,
    severity: Severity,
    message: str,
    record_key: str = "",
    **details: str,
) -> QualityIssue:
    return QualityIssue(
        code=code,
        severity=severity,
        message=message,
        record_key=record_key,
        details=tuple(sorted(details.items())),
    )


def _key(record: object) -> str:
    key = getattr(record, "key", None)
    if callable(key):
        return str(key())
    return str(getattr(record, "event_id", ""))


def _record_clock(record: object) -> tuple[datetime | None, datetime | None]:
    return getattr(record, "event_time", None), getattr(record, "available_at", None)


def _validate_clock(
    record: object,
    *,
    now: datetime | None,
    policy: DataQualityPolicy,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    event_time, available_at = _record_clock(record)
    key = _key(record)
    if event_time is None or available_at is None:
        return [
            _issue(
                QualityCode.MISSING_FIELD,
                Severity.ERROR,
                "record lacks event_time/available_at",
                key,
            )
        ]
    try:
        require_tz(event_time, "event_time")
        require_tz(available_at, "available_at")
    except ValueError:
        return [
            _issue(
                QualityCode.NAIVE_TIMESTAMP,
                Severity.ERROR,
                "record timestamps must be timezone-aware",
                key,
            )
        ]
    if available_at < event_time:
        issues.append(
            _issue(
                QualityCode.TIMESTAMP_ORDER,
                Severity.ERROR,
                "available_at precedes event_time (look-ahead)",
                key,
            )
        )
    if now is not None:
        require_tz(now, "now")
        future_skew = (event_time - now).total_seconds()
        if future_skew > policy.max_future_skew_seconds:
            issues.append(
                _issue(
                    QualityCode.FUTURE_DATA,
                    Severity.ERROR,
                    f"event_time is {future_skew:.3f}s in the future",
                    key,
                )
            )
        if policy.max_age_seconds is not None:
            age = (now - available_at).total_seconds()
            if age > policy.max_age_seconds:
                issues.append(
                    _issue(
                        QualityCode.STALE_DATA,
                        Severity.ERROR if policy.stale_is_error else Severity.WARNING,
                        f"data is {age:.3f}s old (max {policy.max_age_seconds:.3f}s)",
                        key,
                    )
                )
    return issues


def check_record(
    record: object,
    *,
    now: datetime | None = None,
    policy: DataQualityPolicy | None = None,
) -> QualityReport:
    """Check a single canonical or market-data record."""
    policy = policy or DataQualityPolicy()
    key = _key(record)

    if isinstance(record, EventEnvelope):
        issues = _validate_event(record, now=now, policy=policy)
        return QualityReport(issues=tuple(issues), checked=1)
    issues = _validate_clock(record, now=now, policy=policy)

    if isinstance(record, BarRecord):
        prices = (record.open, record.high, record.low, record.close)
        if min(prices) <= 0 or record.low > record.high:
            issues.append(
                _issue(
                    QualityCode.INVALID_OHLC,
                    Severity.ERROR,
                    "bar prices must be positive and low <= high",
                    key,
                )
            )
        if record.volume < 0:
            issues.append(
                _issue(
                    QualityCode.NEGATIVE_VOLUME,
                    Severity.ERROR,
                    "bar volume cannot be negative",
                    key,
                )
            )
    elif isinstance(record, OrderBookSnapshot):
        if not record.bids or not record.asks:
            issues.append(
                _issue(QualityCode.EMPTY_BOOK, Severity.ERROR, "empty order book", key)
            )
        elif record.best_bid >= record.best_ask:
            issues.append(
                _issue(QualityCode.CROSSED_BOOK, Severity.ERROR, "crossed order book", key)
            )
    elif isinstance(record, QuoteTick):
        if record.bid >= record.ask:
            issues.append(
                _issue(QualityCode.CROSSED_BOOK, Severity.ERROR, "crossed quote", key)
            )
    elif isinstance(record, TradeTick):
        if record.side is TradeSide.UNKNOWN:
            issues.append(
                _issue(
                    QualityCode.UNKNOWN_TRADE_SIDE,
                    Severity.ERROR if policy.unknown_trade_side_is_error else Severity.WARNING,
                    "trade aggressor side is unknown; trade-flow features must fail closed",
                    key,
                )
            )
    elif isinstance(record, (FundingRateObservation, OpenInterestObservation)):
        pass
    elif not hasattr(record, "event_time"):
        issues.append(
            _issue(
                QualityCode.SCHEMA_MISMATCH,
                Severity.ERROR,
                f"unsupported record type: {type(record).__name__}",
                key,
            )
        )
    return QualityReport(issues=tuple(issues), checked=1)


def _validate_event(
    event: EventEnvelope,
    *,
    now: datetime | None,
    policy: DataQualityPolicy,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    key = event.event_id
    if event.published_timestamp < event.event_timestamp:
        issues.append(
            _issue(
                QualityCode.TIMESTAMP_ORDER,
                Severity.ERROR,
                "published_timestamp precedes event_timestamp",
                key,
            )
        )
    if event.received_timestamp < event.event_timestamp:
        issues.append(
            _issue(
                QualityCode.TIMESTAMP_ORDER,
                Severity.ERROR,
                "received_timestamp precedes event_timestamp",
                key,
            )
        )
    if event.available_timestamp is None or event.available_timestamp < event.published_timestamp:
        issues.append(
            _issue(
                QualityCode.TIMESTAMP_ORDER,
                Severity.ERROR,
                "available_timestamp precedes publication",
                key,
            )
        )
    if now is not None and policy.max_age_seconds is not None:
        available = event.available_timestamp
        if available is not None:
            age = (now - available).total_seconds()
            if age > policy.max_age_seconds:
                issues.append(
                    _issue(
                        QualityCode.STALE_DATA,
                        Severity.ERROR if policy.stale_is_error else Severity.WARNING,
                        f"event is {age:.3f}s old (max {policy.max_age_seconds:.3f}s)",
                        key,
                    )
                )
    return issues


def check_pit(
    records: Iterable[object],
    *,
    decision_time: datetime,
    require_available: bool = True,
) -> QualityReport:
    """Reject any record that could not have been known at ``decision_time``."""
    require_tz(decision_time, "decision_time")
    issues: list[QualityIssue] = []
    count = 0
    for record in records:
        count += 1
        key = _key(record)
        event_time = getattr(record, "event_time", None) or getattr(
            record, "event_timestamp", None
        )
        available_at = getattr(record, "available_at", None) or getattr(
            record, "available_timestamp", None
        )
        if require_available and available_at is None:
            issues.append(
                _issue(
                    QualityCode.MISSING_FIELD,
                    Severity.ERROR,
                    "record has no available_at; cannot prove point-in-time safety",
                    key,
                )
            )
            continue
        if event_time is not None and event_time > decision_time:
            issues.append(
                _issue(
                    QualityCode.FUTURE_DATA,
                    Severity.ERROR,
                    "event occurs after the decision time",
                    key,
                )
            )
        if available_at is not None and available_at > decision_time:
            issues.append(
                _issue(
                    QualityCode.LOOKAHEAD,
                    Severity.ERROR,
                    "record is not available at the decision time",
                    key,
                )
            )
    return QualityReport(issues=tuple(issues), checked=count)


def assert_pit(records: Iterable[object], *, decision_time: datetime) -> None:
    check_pit(records, decision_time=decision_time).raise_if_invalid()


def check_bar_series(
    bars: Sequence[BarRecord],
    *,
    interval_seconds: float | None = None,
    now: datetime | None = None,
    policy: DataQualityPolicy | None = None,
    require_nonempty: bool = True,
    expected_instrument_id: str | None = None,
) -> QualityReport:
    """Validate ordering, duplicates, completeness and per-bar invariants."""
    policy = policy or DataQualityPolicy()
    if not bars:
        issues = (
            (
                _issue(
                    QualityCode.MISSING_DATA,
                    Severity.ERROR,
                    "bar series is empty",
                ),
            )
            if require_nonempty
            else ()
        )
        return QualityReport(issues=issues, checked=0)

    issues: list[QualityIssue] = []
    seen_keys: set[str] = set()
    seen_times: set[datetime] = set()
    previous: BarRecord | None = None
    for bar in bars:
        report = check_record(bar, now=None, policy=policy)
        issues.extend(report.issues)
        key = bar.key()
        if key in seen_keys:
            issues.append(
                _issue(
                    QualityCode.DUPLICATE_RECORD,
                    Severity.ERROR,
                    "duplicate bar key",
                    key,
                )
            )
        seen_keys.add(key)
        if bar.event_time in seen_times:
            issues.append(
                _issue(
                    QualityCode.DUPLICATE_RECORD,
                    Severity.ERROR,
                    "duplicate bar event_time",
                    key,
                )
            )
        seen_times.add(bar.event_time)
        if expected_instrument_id is not None and bar.instrument_id not in (
            None,
            expected_instrument_id,
        ):
            issues.append(
                _issue(
                    QualityCode.INSTRUMENT_MISMATCH,
                    Severity.ERROR,
                    f"expected instrument {expected_instrument_id!r}",
                    key,
                )
            )
        if previous is not None:
            delta = (bar.event_time - previous.event_time).total_seconds()
            if policy.require_monotonic and delta <= 0:
                issues.append(
                    _issue(
                        QualityCode.OUT_OF_ORDER,
                        Severity.ERROR,
                        "bar timestamps are not strictly increasing",
                        key,
                    )
                )
            if (
                interval_seconds is not None
                and interval_seconds > 0
                and delta > interval_seconds * policy.max_gap_multiplier
            ):
                issues.append(
                    _issue(
                        QualityCode.DATA_GAP,
                        Severity.ERROR,
                        f"bar gap {delta:.1f}s exceeds {interval_seconds:.1f}s interval",
                        key,
                    )
                )
        previous = bar

    if now is not None:
        latest = max(bars, key=lambda bar: bar.available_at)
        issues.extend(_validate_clock(latest, now=now, policy=policy))
    return QualityReport(issues=tuple(issues), checked=len(bars))


def check_trade_series(
    trades: Sequence[TradeTick],
    *,
    now: datetime | None = None,
    policy: DataQualityPolicy | None = None,
    require_nonempty: bool = True,
) -> QualityReport:
    policy = policy or DataQualityPolicy()
    if not trades:
        issues = (
            (
                _issue(
                    QualityCode.MISSING_DATA,
                    Severity.ERROR,
                    "trade series is empty",
                ),
            )
            if require_nonempty
            else ()
        )
        return QualityReport(issues=issues, checked=0)

    issues: list[QualityIssue] = []
    seen_ids: set[tuple[str, str]] = set()
    previous_time: datetime | None = None
    for trade in trades:
        issues.extend(check_record(trade, now=None, policy=policy).issues)
        identity = (trade.venue, trade.trade_id)
        if identity in seen_ids:
            issues.append(
                _issue(
                    QualityCode.DUPLICATE_RECORD,
                    Severity.ERROR,
                    "duplicate trade_id",
                    trade.key(),
                )
            )
        seen_ids.add(identity)
        if previous_time is not None and trade.event_time < previous_time:
            issues.append(
                _issue(
                    QualityCode.OUT_OF_ORDER,
                    Severity.ERROR,
                    "trade timestamps are not monotonic",
                    trade.key(),
                )
            )
        previous_time = trade.event_time
    if now is not None and trades:
        issues.extend(_validate_clock(trades[-1], now=now, policy=policy))
    return QualityReport(issues=tuple(issues), checked=len(trades))


def check_order_book(
    snapshot: OrderBookSnapshot,
    *,
    previous_sequence: int | None = None,
    now: datetime | None = None,
    policy: DataQualityPolicy | None = None,
) -> QualityReport:
    policy = policy or DataQualityPolicy()
    issues = list(check_record(snapshot, now=now, policy=policy).issues)
    if (
        previous_sequence is not None
        and snapshot.sequence is not None
        and snapshot.sequence - previous_sequence > policy.max_sequence_gap
    ):
        issues.append(
            _issue(
                QualityCode.SEQUENCE_GAP,
                Severity.ERROR,
                f"order-book sequence jumped from {previous_sequence} to {snapshot.sequence}",
                snapshot.key(),
            )
        )
    if (
        previous_sequence is not None
        and snapshot.sequence is not None
        and snapshot.sequence <= previous_sequence
    ):
        issues.append(
            _issue(
                QualityCode.SEQUENCE_GAP,
                Severity.ERROR,
                "order-book sequence did not advance",
                snapshot.key(),
            )
        )
    return QualityReport(issues=tuple(issues), checked=1)


def check_timestamp_drift(
    *,
    provider_time: datetime,
    local_time: datetime,
    max_drift_seconds: float,
    record_key: str = "",
) -> QualityReport:
    require_tz(provider_time, "provider_time")
    require_tz(local_time, "local_time")
    if max_drift_seconds < 0:
        raise ValueError("max_drift_seconds cannot be negative")
    drift = abs((local_time - provider_time).total_seconds())
    issues: list[QualityIssue] = []
    if drift > max_drift_seconds:
        issues.append(
            _issue(
                QualityCode.TIMESTAMP_DRIFT,
                Severity.ERROR,
                f"clock drift {drift:.3f}s exceeds {max_drift_seconds:.3f}s",
                record_key,
            )
        )
    return QualityReport(issues=tuple(issues), checked=1)


def require_fields(
    record: object,
    required: Sequence[str],
    *,
    record_key: str = "",
) -> QualityReport:
    issues: list[QualityIssue] = []
    for name in required:
        if not hasattr(record, name) or getattr(record, name) is None:
            issues.append(
                _issue(
                    QualityCode.MISSING_FIELD,
                    Severity.ERROR,
                    f"missing required field {name!r}",
                    record_key,
                )
            )
    return QualityReport(issues=tuple(issues), checked=1)


__all__ = [
    "QUALITY_REPORT_VERSION",
    "DataQualityError",
    "DataQualityPolicy",
    "QualityCode",
    "QualityIssue",
    "QualityReport",
    "Severity",
    "assert_pit",
    "check_bar_series",
    "check_order_book",
    "check_pit",
    "check_record",
    "check_timestamp_drift",
    "check_trade_series",
    "require_fields",
]
