"""Ingestion package: raw storage, canonical storage and the pipeline."""

from __future__ import annotations

from axel.data.ingest.db_store import TimescaleStore, session_factory_from_url
from axel.data.ingest.ohlcv import content_hash, parse_ohlcv_csv
from axel.data.ingest.pipeline import IngestionPipeline, IngestionReport
from axel.data.ingest.raw_store import RawStore
from axel.data.ingest.store import CanonicalStore

__all__ = [
    "CanonicalStore",
    "IngestionPipeline",
    "IngestionReport",
    "RawStore",
    "TimescaleStore",
    "content_hash",
    "parse_ohlcv_csv",
    "session_factory_from_url",
]
