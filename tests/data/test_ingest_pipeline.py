from datetime import UTC, datetime, timedelta

from axel.data.ingest import CanonicalStore, IngestionPipeline, RawStore
from axel.data.providers.base import ProviderResult
from axel.data.schemas import BarRecord, Provenance, RawPayload


def _record() -> BarRecord:
    event = datetime(2026, 1, 2, tzinfo=UTC)
    provenance = Provenance(
        source="alpaca",
        source_id="AAPL:1Day",
        source_hash="hash",
        ingestion_timestamp=datetime(2026, 1, 3, tzinfo=UTC),
    )
    return BarRecord(
        symbol="AAPL",
        venue="alpaca-iex",
        event_time=event,
        available_at=event + timedelta(days=1),
        open=1,
        high=2,
        low=1,
        close=2,
        volume=5,
        adjustment_status="raw",
        provenance=provenance,
    )


def _result() -> ProviderResult:
    body = '{"bars": {"AAPL": [{"t": "2026-01-02T00:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 5}]}}'
    raw = RawPayload(
        source="alpaca",
        request="https://data.alpaca.markets/v2/stocks/bars",
        fetched_at=datetime(2026, 1, 3, tzinfo=UTC),
        body=body,
        context=(("symbol", "AAPL"), ("timeframe", "1Day"), ("venue", "alpaca-iex")),
    )
    return ProviderResult(raw=raw, records=(_record(),))


def _pipeline(tmp_path) -> IngestionPipeline:
    return IngestionPipeline(RawStore(tmp_path / "raw"), CanonicalStore(tmp_path / "canonical"))


def test_ingest_is_idempotent(tmp_path) -> None:
    pipeline = _pipeline(tmp_path)
    first = pipeline.ingest(_result())
    second = pipeline.ingest(_result())
    assert (first.stored, first.duplicates) == (1, 0)
    assert (second.stored, second.duplicates) == (0, 1)
    assert pipeline._canonical_store.count("bar") == 1


def test_raw_payload_is_persisted_once_and_immutable(tmp_path) -> None:
    pipeline = _pipeline(tmp_path)
    raw = _result().raw
    pipeline.ingest(_result())
    raw_path = pipeline._raw_store.path_for(raw)
    assert raw_path.exists()
    snapshot = raw_path.read_text(encoding="utf-8")
    pipeline.ingest(_result())
    assert raw_path.read_text(encoding="utf-8") == snapshot


def test_replay_rederives_records_from_raw(tmp_path) -> None:
    pipeline = _pipeline(tmp_path)
    report = pipeline.replay(_result().raw)
    assert report.stored == 1
    stored = pipeline._canonical_store.all("bar")
    assert len(stored) == 1
    assert stored[0].symbol == "AAPL"
