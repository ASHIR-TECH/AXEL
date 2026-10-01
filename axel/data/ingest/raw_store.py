"""Immutable, content-addressed storage for raw provider payloads (replay/audit)."""

from __future__ import annotations

import json
from pathlib import Path

from axel.data.schemas import RawPayload, parse_dt


class RawStore:
    """Persists each payload exactly once, keyed by its content hash."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def path_for(self, raw: RawPayload) -> Path:
        return self.root / raw.source / f"{raw.content_hash}.json"

    def exists(self, source: str, content_hash: str) -> bool:
        return (self.root / source / f"{content_hash}.json").exists()

    def persist(self, raw: RawPayload) -> Path:
        """Write the payload once; repeated calls with identical content are no-ops."""
        path = self.path_for(raw)
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = json.dumps(
            {
                "source": raw.source,
                "request": raw.request,
                "fetched_at": raw.fetched_at.isoformat(),
                "body": raw.body,
                "context": [list(pair) for pair in raw.context],
            }
        )
        tmp = path.with_name(f"{path.name}.tmp")
        tmp.write_text(envelope, encoding="utf-8")
        tmp.replace(path)
        return path

    def load(self, source: str, content_hash: str) -> RawPayload:
        path = self.root / source / f"{content_hash}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return RawPayload(
            source=data["source"],
            request=data["request"],
            fetched_at=parse_dt(data["fetched_at"]),
            body=data["body"],
            context=tuple((str(k), str(v)) for k, v in data.get("context", [])),
        )
