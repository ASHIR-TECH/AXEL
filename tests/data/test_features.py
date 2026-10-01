from datetime import UTC, datetime, timedelta

import pytest

from axel.data.features import (
    FeatureRecord,
    average_true_range,
    close_price,
    filing_change,
    filing_value,
    keyword_sentiment,
    macro_change,
    macro_value,
    momentum,
    news_count,
    parkinson_volatility,
    rank_by_time,
    realized_volatility,
    rsi,
    simple_return,
    volume_zscore,
    zscore_by_time,
)
from axel.data.schemas import BarRecord, FilingRecord, MacroObservation, NewsRecord, Provenance

START = datetime(2026, 1, 1, tzinfo=UTC)


def _prov(seed: int) -> Provenance:
    return Provenance(
        source="test",
        source_id=str(seed),
        source_hash=f"hash-{seed}",
        ingestion_timestamp=START,
    )


def _bars(count: int, symbol: str = "AAPL", base: float = 100.0) -> list[BarRecord]:
    bars = []
    for index in range(count):
        event = START + timedelta(days=index)
        price = base + index
        bars.append(
            BarRecord(
                symbol=symbol,
                venue="alpaca-iex",
                event_time=event,
                available_at=event,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1000 + index,
                adjustment_status="raw",
                provenance=_prov(index),
            )
        )
    return bars


def _geometric_bars(count: int, rate: float = 0.01, base: float = 100.0) -> list[BarRecord]:
    bars = []
    for index in range(count):
        event = START + timedelta(days=index)
        price = base * (1.0 + rate) ** index
        bars.append(
            BarRecord(
                symbol="AAPL",
                venue="alpaca-iex",
                event_time=event,
                available_at=event,
                open=price,
                high=price * 1.001,
                low=price * 0.999,
                close=price,
                volume=1000.0 + index,
                adjustment_status="raw",
                provenance=_prov(index),
            )
        )
    return bars


def _filing(available: datetime, value: str, accession: str) -> FilingRecord:
    return FilingRecord(
        issuer_id="0000320193",
        accession=accession,
        form="10-Q",
        event_time=datetime(2025, 12, 31, tzinfo=UTC),
        available_at=available,
        fields=(("revenue", value),),
        provenance=_prov(len(accession)),
    )


def _news(title: str, available: datetime, entities: tuple[str, ...] = ("AAPL",)) -> NewsRecord:
    return NewsRecord(
        article_id=title,
        publisher="example.com",
        event_time=available,
        available_at=available,
        title=title,
        body_ref="ref",
        entities=entities,
        provenance=_prov(len(title)),
    )


def test_features_are_deterministic() -> None:
    bars = _bars(30)
    assert simple_return(bars, window=5) == simple_return(bars, window=5)
    assert momentum(bars, window=10) == momentum(bars, window=10)


def test_simple_return_value_and_availability() -> None:
    bars = _bars(3, base=100.0)
    features = simple_return(bars, window=1)
    assert len(features) == 2
    assert features[0].value == pytest.approx(0.01)
    assert features[0].available_at == bars[1].available_at
    assert features[0].event_time == bars[1].event_time


def test_no_lookahead_availability_never_precedes_event() -> None:
    bars = _bars(40)
    produced = [
        *close_price(bars),
        *simple_return(bars, window=3),
        *momentum(bars, window=10),
        *rsi(bars, window=14),
        *realized_volatility(bars, window=10),
        *average_true_range(bars, window=14),
        *parkinson_volatility(bars, window=10),
        *volume_zscore(bars, window=10),
    ]
    assert produced
    assert all(feature.available_at >= feature.event_time for feature in produced)
    assert all(len(feature.fingerprint()) == 64 for feature in produced)


def test_rsi_is_100_for_monotonic_up_series() -> None:
    features = rsi(_bars(20), window=14)
    assert features
    assert all(feature.value == pytest.approx(100.0) for feature in features)


def test_realized_volatility_is_zero_for_constant_returns() -> None:
    features = realized_volatility(_geometric_bars(30), window=10)
    assert features
    assert all(feature.value == pytest.approx(0.0) for feature in features)


def test_parkinson_volatility_is_positive() -> None:
    features = parkinson_volatility(_bars(30), window=10)
    assert features
    assert all(feature.value > 0 for feature in features)


def test_filing_features_use_public_filing_time() -> None:
    first = _filing(datetime(2026, 2, 1, tzinfo=UTC), "100", "a1")
    second = _filing(datetime(2026, 5, 1, tzinfo=UTC), "150", "a2")
    values = filing_value([first, second], field="revenue")
    assert [feature.value for feature in values] == [100.0, 150.0]
    changes = filing_change([first, second], field="revenue")
    assert changes[0].value == pytest.approx(50.0)
    assert changes[0].available_at == datetime(2026, 5, 1, tzinfo=UTC)


def test_macro_change_is_available_only_at_later_vintage() -> None:
    early = MacroObservation(
        series_id="DFF",
        event_time=datetime(2025, 11, 1, tzinfo=UTC),
        available_at=datetime(2025, 12, 15, tzinfo=UTC),
        value=4.0,
        vintage="2025-12-15",
        provenance=_prov(1),
    )
    late = MacroObservation(
        series_id="DFF",
        event_time=datetime(2025, 12, 1, tzinfo=UTC),
        available_at=datetime(2026, 1, 15, tzinfo=UTC),
        value=4.5,
        vintage="2026-01-15",
        provenance=_prov(2),
    )
    assert [feature.value for feature in macro_value([early, late])] == [4.0, 4.5]
    changes = macro_change([early, late])
    assert changes[0].value == pytest.approx(0.5)
    assert changes[0].available_at == datetime(2026, 1, 15, tzinfo=UTC)


def test_keyword_sentiment_is_deterministic_and_llm_free() -> None:
    positive = keyword_sentiment([_news("AAPL beats and rallies", START)])[0]
    negative = keyword_sentiment([_news("AAPL misses and plunges", START)])[0]
    assert positive.value == pytest.approx(1.0)
    assert negative.value == pytest.approx(-1.0)


def test_news_count_rolls_within_window() -> None:
    first = _news("one", START)
    second = _news("two", START + timedelta(hours=2))
    third = _news("three", START + timedelta(days=5))
    counts = news_count([first, second, third], window=timedelta(days=1))
    assert [feature.value for feature in counts] == [1.0, 2.0, 1.0]


def test_cross_sectional_zscore_and_rank() -> None:
    def feature(entity: str, value: float) -> FeatureRecord:
        return FeatureRecord(
            name="momentum_5",
            entity=entity,
            event_time=START,
            available_at=START + timedelta(hours=1),
            value=value,
            params=(),
        )

    features = [feature("AAA", 1.0), feature("BBB", 3.0)]
    zscores = zscore_by_time(features)
    assert [round(item.value, 6) for item in zscores] == [-1.0, 1.0]
    assert all(item.available_at == START + timedelta(hours=1) for item in zscores)
    ranks = rank_by_time(features)
    assert [item.value for item in ranks] == [0.0, 1.0]
