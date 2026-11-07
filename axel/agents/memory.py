"""
Append-only decision and reflection memory for agent review loops.

Adapted from the markdown decision log in TradingAgents
(https://github.com/TauricResearch/TradingAgents,
tradingagents/agents/utils/memory.py), rebuilt as a typed, append-only JSONL
store with point-in-time filtering.

Why point-in-time matters: a backtest must not learn from an outcome that had
not resolved yet. ``past_context(as_of=...)`` therefore includes a reflection
only when its ``resolved_date`` is on or before ``as_of``.

This module is storage and prompt-context only. It never places orders and is
not imported by the deterministic risk/execution/portfolio packages.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal


class MemoryFormatError(ValueError):
    """Raised when a persisted memory line is not a valid record."""


@dataclass(frozen=True)
class DecisionEntry:
    """One immutable agent decision."""

    entry_id: str
    ticker: str
    decision_date: str
    decision: str
    kind: Literal["decision"] = "decision"


@dataclass(frozen=True)
class ReflectionEntry:
    """One immutable reflection that resolves a prior decision."""

    entry_id: str
    decision_id: str
    ticker: str
    resolved_date: str
    reflection: str
    realized_return: float | None = None
    alpha_return: float | None = None
    kind: Literal["reflection"] = "reflection"


MemoryEntry = DecisionEntry | ReflectionEntry


def _iso_date(value: str | date, name: str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def _digest(*parts: str) -> str:
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def decision_id(ticker: str, decision_date: str, decision: str) -> str:
    """Deterministic idempotency key for a decision."""
    return _digest("decision", ticker.strip().upper(), decision_date, " ".join(decision.split()))


def reflection_id(decision_id_value: str, resolved_date: str, reflection: str) -> str:
    """Deterministic idempotency key for a reflection."""
    return _digest("reflection", decision_id_value, resolved_date, " ".join(reflection.split()))


def _serialize(entry: MemoryEntry) -> str:
    return json.dumps(asdict(entry), sort_keys=True, separators=(",", ":"))


def _deserialize(line: str, line_number: int) -> MemoryEntry:
    try:
        payload: dict[str, Any] = json.loads(line)
    except json.JSONDecodeError as exc:
        raise MemoryFormatError(f"invalid JSON on line {line_number}: {exc.msg}") from exc
    kind = payload.get("kind")
    try:
        if kind == "decision":
            return DecisionEntry(
                entry_id=str(payload["entry_id"]),
                ticker=str(payload["ticker"]),
                decision_date=str(payload["decision_date"]),
                decision=str(payload["decision"]),
            )
        if kind == "reflection":
            return ReflectionEntry(
                entry_id=str(payload["entry_id"]),
                decision_id=str(payload["decision_id"]),
                ticker=str(payload["ticker"]),
                resolved_date=str(payload["resolved_date"]),
                reflection=str(payload["reflection"]),
                realized_return=payload.get("realized_return"),
                alpha_return=payload.get("alpha_return"),
            )
    except KeyError as exc:
        raise MemoryFormatError(f"missing field {exc.args[0]!r} on line {line_number}") from exc
    raise MemoryFormatError(f"unknown entry kind {kind!r} on line {line_number}")


class DecisionMemory:
    """Append-only memory store; pass ``path=None`` for an in-memory instance."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path).expanduser() if path is not None else None
        self._records: list[MemoryEntry] = []
        if self._path is not None and self._path.exists():
            self._records = list(self._read(self._path))

    # --- Read path ---------------------------------------------------------

    @staticmethod
    def _read(path: Path) -> Iterable[MemoryEntry]:
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if stripped:
                    yield _deserialize(stripped, number)

    def entries(self) -> tuple[MemoryEntry, ...]:
        """All records in append order."""
        return tuple(self._records)

    def decisions(self) -> tuple[DecisionEntry, ...]:
        return tuple(entry for entry in self._records if isinstance(entry, DecisionEntry))

    def reflections(self) -> tuple[ReflectionEntry, ...]:
        return tuple(entry for entry in self._records if isinstance(entry, ReflectionEntry))

    def _ids(self) -> set[str]:
        return {entry.entry_id for entry in self._records}

    # --- Write path --------------------------------------------------------

    def _persist(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(f"{_serialize(entry)}\n" for entry in self._records)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self._path.name}.", dir=self._path.parent, text=True
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def append_decision(
        self,
        ticker: str,
        decision_date: str | date,
        decision: str,
        *,
        entry_id: str | None = None,
    ) -> bool:
        """Append a decision once; returns False when it is already stored."""
        if not ticker.strip():
            raise ValueError("ticker must not be empty")
        if not decision.strip():
            raise ValueError("decision must not be empty")
        normalized_date = _iso_date(decision_date, "decision_date")
        identifier = entry_id or decision_id(ticker, normalized_date, decision)
        if identifier in self._ids():
            return False
        self._records.append(
            DecisionEntry(
                entry_id=identifier,
                ticker=ticker.strip().upper(),
                decision_date=normalized_date,
                decision=decision.strip(),
            )
        )
        self._persist()
        return True

    def append_reflection(
        self,
        decision_id_value: str,
        resolved_date: str | date,
        reflection: str,
        *,
        realized_return: float | None = None,
        alpha_return: float | None = None,
        entry_id: str | None = None,
    ) -> bool:
        """Append a reflection once; returns False when it is already stored."""
        decision = next(
            (entry for entry in self._records if entry.entry_id == decision_id_value),
            None,
        )
        if not isinstance(decision, DecisionEntry):
            raise KeyError(f"unknown decision_id: {decision_id_value}")
        if not reflection.strip():
            raise ValueError("reflection must not be empty")
        normalized_date = _iso_date(resolved_date, "resolved_date")
        if normalized_date < decision.decision_date:
            raise ValueError("resolved_date must not precede decision_date")
        identifier = entry_id or reflection_id(
            decision_id_value, normalized_date, reflection
        )
        if identifier in self._ids():
            return False
        self._records.append(
            ReflectionEntry(
                entry_id=identifier,
                decision_id=decision_id_value,
                ticker=decision.ticker,
                resolved_date=normalized_date,
                reflection=reflection.strip(),
                realized_return=realized_return,
                alpha_return=alpha_return,
            )
        )
        self._persist()
        return True

    # --- Prompt context ----------------------------------------------------

    def _resolved_pairs(self, as_of: str | date | None) -> list[tuple[DecisionEntry, ReflectionEntry]]:
        cutoff = _iso_date(as_of, "as_of") if as_of is not None else None
        by_id = {entry.entry_id: entry for entry in self.decisions()}
        pairs: list[tuple[DecisionEntry, ReflectionEntry]] = []
        for reflection in self.reflections():
            decision = by_id.get(reflection.decision_id)
            if decision is None:
                continue
            if cutoff is not None and reflection.resolved_date > cutoff:
                continue
            pairs.append((decision, reflection))
        return pairs

    def past_context(
        self,
        ticker: str,
        *,
        as_of: str | date | None = None,
        n_same: int = 5,
        n_cross: int = 3,
    ) -> str:
        """Format resolved lessons for prompt injection, most recent first.

        Only reflections whose ``resolved_date`` is on or before ``as_of`` are
        included, so historical runs cannot see the future.
        """
        if n_same < 0 or n_cross < 0:
            raise ValueError("n_same and n_cross must be non-negative")
        symbol = ticker.strip().upper()
        pairs = self._resolved_pairs(as_of)

        same: list[tuple[DecisionEntry, ReflectionEntry]] = []
        cross: list[tuple[DecisionEntry, ReflectionEntry]] = []
        for decision, reflection in reversed(pairs):
            if decision.ticker == symbol and len(same) < n_same:
                same.append((decision, reflection))
            elif decision.ticker != symbol and len(cross) < n_cross:
                cross.append((decision, reflection))

        parts: list[str] = []
        if same:
            parts.append(f"Past analyses of {symbol} (most recent first):")
            parts.extend(self._format(decision, reflection) for decision, reflection in same)
        if cross:
            parts.append("Recent cross-ticker lessons:")
            parts.extend(self._format(decision, reflection) for decision, reflection in cross)
        return "\n\n".join(parts)

    @staticmethod
    def _format(decision: DecisionEntry, reflection: ReflectionEntry) -> str:
        outcome = f"resolved {reflection.resolved_date}"
        if reflection.realized_return is not None:
            outcome += f", return {reflection.realized_return:+.2%}"
        if reflection.alpha_return is not None:
            outcome += f", alpha {reflection.alpha_return:+.2%}"
        return (
            f"[{decision.decision_date}] {decision.decision}\n"
            f"  reflection ({outcome}): {reflection.reflection}"
        )
