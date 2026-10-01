from datetime import UTC, datetime

import pytest

from axel.data.point_in_time import (
    LookaheadError,
    PointInTimeIndex,
    as_of,
    assert_no_lookahead,
    available_at,
    feature_available_at,
    filter_available,
)
from axel.data.schemas import FilingRecord, Provenance


def _filing(available: datetime, accession: str = "0001") -> FilingRecord:
    return FilingRecord(
        issuer_id="0000320193",
        accession=accession,
        form="10-Q",
        event_time=datetime(2025, 12, 31, tzinfo=UTC),
        available_at=available,
        fields=(),
        provenance=Provenance(
            source="sec",
            source_id=accession,
            source_hash=accession,
            ingestion_timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        ),
    )


def test_available_at_is_the_max_of_dependencies() -> None:
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 1, 2, tzinfo=UTC)
    assert available_at(early, late) == late
    assert feature_available_at(early, [late]) == late


def test_as_of_excludes_future_and_selects_latest() -> None:
    early = _filing(datetime(2026, 2, 1, tzinfo=UTC), accession="a1")
    late = _filing(datetime(2026, 2, 20, tzinfo=UTC), accession="a2")
    assert as_of([early, late], at=datetime(2026, 2, 10, tzinfo=UTC)) is early
    assert as_of([early, late], at=datetime(2026, 2, 25, tzinfo=UTC)) is late
    assert filter_available([early, late], at=datetime(2026, 2, 5, tzinfo=UTC)) == [early]


def test_future_filing_injection_is_blocked() -> None:
    future = _filing(datetime(2026, 3, 1, tzinfo=UTC))
    with pytest.raises(LookaheadError):
        assert_no_lookahead(future, at=datetime(2026, 2, 1, tzinfo=UTC))


def test_point_in_time_index_resolves_revisions() -> None:
    early = _filing(datetime(2026, 2, 1, tzinfo=UTC), accession="a1")
    revised = _filing(datetime(2026, 2, 20, tzinfo=UTC), accession="a1")
    index = PointInTimeIndex([early, revised])
    assert index.get(early.key(), at=datetime(2026, 2, 10, tzinfo=UTC)) is early
    assert index.get(early.key(), at=datetime(2026, 2, 25, tzinfo=UTC)) is revised
