"""
News-derived deterministic features.

News text is UNTRUSTED DATA: it is scored by a fixed keyword lexicon, never by
an LLM, so the result is reproducible and cannot itself drive execution.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import timedelta

from axel.data.features.base import FeatureRecord, param
from axel.data.schemas import NewsRecord

NEWS_LEXICON_VERSION = "lex-1"

_POSITIVE = frozenset(
    {
        "beat",
        "beats",
        "surge",
        "surges",
        "rally",
        "rallies",
        "growth",
        "upgrade",
        "upgrades",
        "record",
        "strong",
        "gain",
        "gains",
        "profit",
        "profits",
    }
)
_NEGATIVE = frozenset(
    {
        "miss",
        "misses",
        "drop",
        "drops",
        "plunge",
        "plunges",
        "downgrade",
        "downgrades",
        "weak",
        "loss",
        "losses",
        "fall",
        "falls",
        "cut",
        "cuts",
        "probe",
        "lawsuit",
    }
)

_TOKEN = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return [token for token in _TOKEN.split(text.lower()) if token]


def keyword_sentiment(news: Sequence[NewsRecord]) -> list[FeatureRecord]:
    """Net keyword polarity in [-1, 1] per article, available at publish time."""
    ordered = sorted(news, key=lambda article: article.available_at)
    params = param(lexicon=NEWS_LEXICON_VERSION)
    features: list[FeatureRecord] = []
    for article in ordered:
        tokens = _tokens(article.title)
        positive = sum(token in _POSITIVE for token in tokens)
        negative = sum(token in _NEGATIVE for token in tokens)
        total = positive + negative
        value = (positive - negative) / total if total else 0.0
        entity = article.entities[0] if article.entities else "MARKET"
        features.append(
            FeatureRecord(
                name="news_sentiment",
                entity=entity,
                event_time=article.event_time,
                available_at=article.available_at,
                value=value,
                inputs=(article.provenance.source_hash,),
                params=params,
            )
        )
    return features


def news_count(
    news: Sequence[NewsRecord], *, window: timedelta = timedelta(days=1)
) -> list[FeatureRecord]:
    """Rolling count of articles within ``window`` ending at each article time."""
    if window <= timedelta(0):
        raise ValueError("window must be positive")
    ordered = sorted(news, key=lambda article: article.event_time)
    params = param(window_seconds=int(window.total_seconds()))
    features: list[FeatureRecord] = []
    for index, article in enumerate(ordered):
        cutoff = article.event_time - window
        counted = [item for item in ordered[: index + 1] if item.event_time >= cutoff]
        entity = article.entities[0] if article.entities else "MARKET"
        features.append(
            FeatureRecord(
                name="news_count",
                entity=entity,
                event_time=article.event_time,
                available_at=max(item.available_at for item in counted),
                value=float(len(counted)),
                inputs=tuple(item.provenance.source_hash for item in counted),
                params=params,
            )
        )
    return features
