"""Ingest worker: durable synthetic market and macro data.

This worker only writes data.  It never evaluates risk or submits orders.
Every bar is persisted before any downstream worker can read it, so the
dashboard reflects durable records rather than browser state.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from axel.core.config import settings
from axel.db.models import NewsItem, OhlcvBar
from axel.runtime import audit
from axel.runtime.paper_feed import (
    all_symbols,
    latest_prices,
    next_bar,
    persist_bars,
)

_NEWS_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("fred", "FRED · macro", "Policy-rate path unchanged; front-end yields drift lower."),
    ("sec", "EDGAR · filings", "Routine 8-K filed; no material change to the operating thesis."),
    ("gdelt", "NEWS · gdelt", "Coverage tone improves across large-cap technology."),
    ("kalshi", "KALSHI · odds", "Contract odds reprice after the latest release."),
)

NEWS_INTERVAL_SECONDS = 300


def _ensure_news(session: Session, now: datetime, seed: int) -> int:
    """Write one macro/news row per source per five-minute bucket."""
    bucket = int(now.timestamp() // NEWS_INTERVAL_SECONDS)
    created = 0
    for source, title, body in _NEWS_SOURCES:
        digest = hashlib.sha256(f"{seed}:{source}:{bucket}".encode()).hexdigest()[:24]
        identifier = f"news_{source}_{bucket}"
        if session.get(NewsItem, identifier) is not None:
            continue
        session.add(
            NewsItem(
                id=identifier,
                published_at=now,
                ingested_at=now,
                source=source,
                title=title,
                content=f"{body} ({digest})",
                content_hash=hashlib.sha256(identifier.encode()).hexdigest(),
                trust_flag=source == "fred",
            )
        )
        created += 1
    return created


def ingest_once(
    session: Session,
    *,
    now: datetime | None = None,
    interval_seconds: float | None = None,
    seed: int | None = None,
) -> dict[str, int]:
    """Generate and persist one bar per symbol plus periodic macro records."""
    stamp = now or datetime.now(UTC)
    interval = interval_seconds or settings.paper_runtime_interval_seconds
    run_seed = seed if seed is not None else settings.paper_runtime_seed

    symbols = all_symbols()
    # One query for every previous close, one batched insert for the cycle.
    # The previous implementation issued two SELECTs and one INSERT per bar.
    previous = latest_prices(session, symbols)
    bars = [
        next_bar(
            session,
            symbol,
            stamp,
            interval_seconds=interval,
            seed=run_seed,
            previous_price=previous.get(symbol),
        )
        for symbol in symbols
    ]
    written = persist_bars(session, bars, interval_seconds=interval)

    news = _ensure_news(session, stamp, run_seed)
    audit.record(
        session,
        event_type="market.ingest",
        actor="axel.data.ingest",
        message=(
            f"ingest cycle · {written} symbols · {news} macro records · "
            f"interval={interval:.1f}s"
        ),
        logger="axel.data.ingest",
        channel="SYS",
        level="INFO",
        timestamp=stamp,
        symbols=written,
        macro_records=news,
    )
    return {"bars": written, "news": news}


def bar_count(session: Session) -> int:
    from sqlalchemy import func

    return int(session.execute(select(func.count()).select_from(OhlcvBar)).scalar_one())


__all__ = ["NEWS_INTERVAL_SECONDS", "bar_count", "ingest_once"]
