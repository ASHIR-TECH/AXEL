"""
End-to-end verification of the Phase 2 pipeline against *live* provider APIs.

The automated test suite feeds every provider from recorded payloads, which
proves the parsers are correct but says nothing about whether today's upstream
APIs still return what we assume. This script closes that gap: it performs a
single read-only fetch per source, pushes the real response through the real
ingestion pipeline, and asserts the Phase 2 invariants on the result.

It is deliberately read-only. It never posts an order, never writes outside
``.phase2_verify/``, and never prints a credential.

Usage::

    .venv/bin/python scripts/verify_phase2_pipeline.py
    .venv/bin/python scripts/verify_phase2_pipeline.py --symbol SPY --series DGS10
    .venv/bin/python scripts/verify_phase2_pipeline.py --source fred

Exit code is 0 only if every selected check passed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from axel.core.config import settings
from axel.data.ingest.pipeline import IngestionPipeline, IngestionReport
from axel.data.ingest.raw_store import RawStore
from axel.data.ingest.store import CanonicalStore
from axel.data.ml.calendars import calendar_for_bars
from axel.data.ml.risk_model import (
    StopDistanceRiskModel,
    VolatilityRiskModel,
    realized_volatility,
)
from axel.data.providers.alpaca import AlpacaMarketData
from axel.data.providers.fred import FredData
from axel.data.providers.sec import SecEdgar
from axel.data.schemas import BarRecord, CanonicalRecord

WORK_DIR = Path(".phase2_verify")
DEFAULT_SYMBOL = "SPY"
DEFAULT_SERIES = "DGS10"
DEFAULT_CIK = "0000320193"
LOOKBACK_DAYS = 45


class CheckFailure(Exception):
    """A verification invariant did not hold."""


class Checks:
    """Collects named pass/fail results so one failure does not hide the rest."""

    def __init__(self) -> None:
        self._results: list[tuple[str, bool, str]] = []

    def record(self, name: str, passed: bool, detail: str = "") -> bool:
        self._results.append((name, passed, detail))
        return passed

    def expect(self, name: str, condition: bool, detail: str = "") -> bool:
        return self.record(name, bool(condition), detail)

    def run(self, name: str, check: Callable[[], str | None]) -> None:
        """Run ``check``; a raised exception is a failure, not a crash."""
        try:
            detail = check() or ""
        except CheckFailure as exc:
            self.record(name, False, str(exc))
        except Exception as exc:  # noqa: BLE001 - surface as a failed check
            self.record(name, False, f"{type(exc).__name__}: {exc}")
        else:
            self.record(name, True, detail)

    @property
    def failures(self) -> list[tuple[str, bool, str]]:
        return [row for row in self._results if not row[1]]

    def report(self) -> int:
        print("=" * 72)
        print("AXEL Phase 2 — live provider verification")
        print("=" * 72)
        for name, passed, detail in self._results:
            mark = "PASS" if passed else "FAIL"
            suffix = f" — {detail}" if detail else ""
            print(f"  [{mark}] {name}{suffix}")
        print("-" * 72)
        total = len(self._results)
        failed = len(self.failures)
        if failed:
            print(f"  {failed} of {total} checks FAILED")
            print("=" * 72)
            return 1
        print(f"  all {total} checks passed")
        print("=" * 72)
        return 0


def canonical_count(store: CanonicalStore, record_type: str) -> int:
    """How many records of a type the canonical store currently holds."""
    return len(store.all(record_type))


def _window(days: int) -> tuple[datetime, datetime]:
    """A trailing window that ends yesterday, so the last bar is settled."""
    end = datetime.now(UTC) - timedelta(days=1)
    return end - timedelta(days=days), end


def _assert_no_lookahead(records: list[CanonicalRecord]) -> str:
    """Every record must be visible no earlier than its economic event."""
    bad = [r for r in records if r.available_at < r.event_time]
    if bad:
        raise CheckFailure(
            f"{len(bad)} record(s) with available_at < event_time, "
            f"first: {bad[0].record_type} {bad[0].event_time}"
        )
    return f"{len(records)} record(s), all available_at >= event_time"


def _assert_tz_aware(records: list[CanonicalRecord]) -> str:
    naive = [r for r in records if r.event_time.tzinfo is None]
    if naive:
        raise CheckFailure(f"{len(naive)} record(s) with a naive event_time")
    return "all timestamps timezone-aware"


def _assert_bars_settled(bars: list[BarRecord], timeframe: str) -> str:
    """Daily bars must not become readable before the session they describe ends."""
    if len(bars) < 2:
        raise CheckFailure(f"only {len(bars)} bar(s) returned; cannot verify availability")
    stale = [b for b in bars if b.available_at <= b.event_time]
    if stale:
        raise CheckFailure(
            f"{len(stale)} daily bar(s) readable at or before event_time; "
            "an unfinished candle would be tradable"
        )
    ordered = all(
        bars[i].event_time < bars[i + 1].event_time for i in range(len(bars) - 1)
    )
    if not ordered:
        raise CheckFailure("bars are not strictly ordered by event_time")
    return f"{len(bars)} bar(s), ordered, each published after its session closes"


def _assert_calendar(bars: list[BarRecord]) -> str:
    calendar = calendar_for_bars(bars)
    if calendar is None:
        raise CheckFailure(f"no calendar inferred for venue {bars[0].venue!r}")
    sessions = calendar.sessions_between(
        bars[0].event_time.date(), bars[-1].event_time.date()
    )
    if sessions < 2:
        raise CheckFailure("fewer than 2 sessions inferred across the fetched window")
    return f"{sessions} sessions over the window, venue {bars[0].venue!r}"


def _assert_risk_model(bars: list[BarRecord]) -> str:
    """The risk denominator must be a real number, not a fixed account fraction."""
    vol = realized_volatility(bars)
    if vol <= 0:
        raise CheckFailure("realized volatility is zero; series is too short or flat")
    close = bars[-1].close
    vol_model = VolatilityRiskModel()
    stop_model = StopDistanceRiskModel(stop_fraction=0.02)
    history = bars[-21:]
    vol_risk = vol_model.risk(symbol=bars[0].symbol, quantity=10, price=close, history=history)
    stop_risk = stop_model.risk(
        symbol=bars[0].symbol, quantity=10, price=close, history=history
    )
    if vol_risk <= 0 or stop_risk <= 0:
        raise CheckFailure("risk models returned a non-positive risk unit")
    return (
        f"realized vol {vol:.4%}, 10-share risk unit "
        f"{vol_risk:.2f} (vol) / {stop_risk:.2f} (stop)"
    )


def verify_alpaca(
    checks: Checks, symbol: str, pipeline: IngestionPipeline, store: CanonicalStore
) -> None:
    api_key = settings.alpaca_api_key
    secret_key = settings.alpaca_secret_key
    if not api_key or not secret_key:
        checks.record("alpaca: credentials configured", False, "missing ALPACA_API_KEY/SECRET_KEY")
        return
    provider = AlpacaMarketData(api_key.get_secret_value(), secret_key.get_secret_value())
    start, end = _window(LOOKBACK_DAYS)

    result = provider.fetch_bars(symbol, start, end, timeframe="1Day")
    bars = [r for r in result.records if isinstance(r, BarRecord)]
    checks.expect(
        "alpaca: returned bars",
        bool(bars),
        f"{len(bars)} daily bar(s) for {symbol}",
    )
    if not bars:
        return
    checks.expect("alpaca: bar availability", True, _assert_bars_settled(bars, "1Day"))
    checks.expect("alpaca: timezone aware", True, _assert_tz_aware(bars))
    checks.expect("alpaca: session calendar", True, _assert_calendar(bars))
    checks.expect("alpaca: risk denominator", True, _assert_risk_model(bars))

    report: IngestionReport = pipeline.ingest(result)
    checks.expect(
        "alpaca: every bar accounted for",
        report.rejected == 0 and report.stored + report.duplicates == len(bars),
        f"stored={report.stored} duplicates={report.duplicates} rejected={report.rejected}",
    )
    checks.expect(
        "alpaca: bars present in store",
        canonical_count(store, "bar") >= len(bars),
        f"{canonical_count(store, 'bar')} bar(s) held",
    )
    replay: IngestionReport = pipeline.replay(result.raw)
    checks.expect(
        "alpaca: replay is a no-op",
        replay.stored == 0 and replay.duplicates == len(bars),
        f"replay stored={replay.stored} duplicates={replay.duplicates}",
    )


def verify_fred(
    checks: Checks,
    series: str,
    pipeline: IngestionPipeline,
    store: CanonicalStore,
    as_of: datetime,
) -> None:
    api_key = settings.fred_api_key
    if not api_key:
        checks.record("fred: credentials configured", False, "missing FRED_API_KEY")
        return
    provider = FredData(api_key.get_secret_value())
    vintage = as_of.strftime("%Y-%m-%d")

    result = provider.fetch_observations(series, realtime_as_of=vintage)
    records = list(result.records)
    checks.expect("fred: returned observations", bool(records), f"{len(records)} observation(s)")
    if not records:
        return
    checks.expect("fred: no lookahead", True, _assert_no_lookahead(records))
    checks.expect("fred: timezone aware", True, _assert_tz_aware(records))

    report = pipeline.ingest(result)
    checks.expect(
        "fred: every observation accounted for",
        report.rejected == 0 and report.stored + report.duplicates == len(records),
        f"stored={report.stored} duplicates={report.duplicates} rejected={report.rejected}",
    )
    checks.expect(
        "fred: observations present in store",
        canonical_count(store, "macro") >= len(records),
        f"{canonical_count(store, 'macro')} observation(s) held",
    )
    replay = pipeline.replay(result.raw)
    checks.expect(
        "fred: replay is a no-op",
        replay.stored == 0,
        f"replay stored={replay.stored} duplicates={replay.duplicates}",
    )


def verify_sec(checks: Checks, cik: str, pipeline: IngestionPipeline) -> None:
    user_agent = settings.sec_user_agent
    if not user_agent:
        checks.record("sec: user agent configured", False, "missing SEC_USER_AGENT")
        return
    provider = SecEdgar(user_agent)
    result = provider.fetch_filings(cik)
    records = list(result.records)
    checks.expect("sec: returned filings", bool(records), f"{len(records)} filing(s) for {cik}")
    if not records:
        return
    checks.expect("sec: no lookahead", True, _assert_no_lookahead(records))

    report = pipeline.ingest(result)
    checks.expect(
        "sec: every filing accounted for",
        report.rejected == 0 and report.stored + report.duplicates == len(records),
        f"stored={report.stored} duplicates={report.duplicates} rejected={report.rejected}",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the Phase 2 pipeline against live, read-only provider APIs"
    )
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Alpaca IEX symbol")
    parser.add_argument("--series", default=DEFAULT_SERIES, help="FRED series id")
    parser.add_argument("--cik", default=DEFAULT_CIK, help="SEC issuer CIK")
    parser.add_argument(
        "--source",
        choices=("all", "alpaca", "fred", "sec"),
        default="all",
        help="restrict verification to one provider",
    )
    args = parser.parse_args()

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    raw_store = RawStore(WORK_DIR / "raw")
    canonical_store = CanonicalStore(WORK_DIR)
    pipeline = IngestionPipeline(raw_store, canonical_store)

    checks = Checks()
    print(
        f"work dir: {WORK_DIR.resolve()}   (read-only against every provider)\n"
        f"window:   trailing {LOOKBACK_DAYS} days ending yesterday\n"
    )

    if args.source in ("all", "alpaca"):
        print("--- Alpaca IEX bars")
        verify_alpaca(checks, args.symbol, pipeline, canonical_store)
    if args.source in ("all", "fred"):
        print("--- FRED macro (vintage pinned)")
        verify_fred(
            checks, args.series, pipeline, canonical_store, datetime.now(UTC) - timedelta(days=1)
        )
    if args.source in ("all", "sec"):
        print("--- SEC EDGAR filings")
        verify_sec(checks, args.cik, pipeline)

    return checks.report()


if __name__ == "__main__":
    sys.exit(main())
