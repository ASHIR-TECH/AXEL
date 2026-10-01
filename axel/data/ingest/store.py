"""Deduplicating, append-only store for canonical records."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from axel.data.schemas import CanonicalRecord, record_from_dict, record_key, record_to_dict


class CanonicalStore:
    """Keeps canonical records unique by logical key; optionally durable as JSONL."""

    def __init__(self, root: Path | str | None = None) -> None:
        self._records: list[CanonicalRecord] = []
        self._index: dict[str, CanonicalRecord] = {}
        self._path: Path | None = Path(root) / "canonical.jsonl" if root else None
        if self._path is not None and self._path.exists():
            self._load()

    def _load(self) -> None:
        assert self._path is not None
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = record_from_dict(json.loads(line))
            self._index[record_key(record)] = record
            self._records.append(record)

    def put(self, record: CanonicalRecord) -> bool:
        """Store the record; return True if new, False if a duplicate of an existing key."""
        key = record_key(record)
        if key in self._index:
            return False
        self._index[key] = record
        self._records.append(record)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record_to_dict(record)) + "\n")
        return True

    def has(self, record: CanonicalRecord) -> bool:
        return record_key(record) in self._index

    def get(self, record: CanonicalRecord) -> CanonicalRecord | None:
        return self._index.get(record_key(record))

    def all(self, record_type: str | None = None) -> list[CanonicalRecord]:
        if record_type is None:
            return list(self._records)
        return [r for r in self._records if r.record_type == record_type]

    def count(self, record_type: str | None = None) -> int:
        return len(self.all(record_type))

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[CanonicalRecord]:
        return iter(self._records)
