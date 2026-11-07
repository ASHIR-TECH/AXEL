"""Tests for the append-only decision/reflection memory."""

import json
from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from axel.agents.memory import (
    DecisionEntry,
    DecisionMemory,
    MemoryFormatError,
    ReflectionEntry,
    decision_id,
    reflection_id,
)


def _decision_id(memory: DecisionMemory, ticker: str, when: str, text: str) -> str:
    memory.append_decision(ticker, when, text)
    return decision_id(ticker, when, text)


def test_append_decision_is_idempotent() -> None:
    memory = DecisionMemory()

    assert memory.append_decision("btc", "2026-01-01", "Buy the breakout") is True
    assert memory.append_decision("BTC", "2026-01-01", "Buy the breakout") is False

    assert len(memory.entries()) == 1
    entry = memory.decisions()[0]
    assert isinstance(entry, DecisionEntry)
    assert entry.ticker == "BTC"
    assert entry.decision_date == "2026-01-01"
    assert entry.decision == "Buy the breakout"


def test_decision_ids_are_normalized_and_deterministic() -> None:
    assert decision_id("btc", "2026-01-01", "Buy   now") == decision_id(
        " BTC ", "2026-01-01", "Buy now"
    )
    assert decision_id("BTC", "2026-01-01", "Buy now") != decision_id(
        "BTC", "2026-01-02", "Buy now"
    )


def test_append_reflection_requires_a_known_decision() -> None:
    memory = DecisionMemory()

    with pytest.raises(KeyError):
        memory.append_reflection("missing", "2026-01-02", "lesson")


def test_append_reflection_is_idempotent_and_linked() -> None:
    memory = DecisionMemory()
    identifier = _decision_id(memory, "BTC", "2026-01-01", "Buy")

    assert (
        memory.append_reflection(
            identifier,
            "2026-01-10",
            "Chased the entry",
            realized_return=0.02,
            alpha_return=0.01,
        )
        is True
    )
    assert (
        memory.append_reflection(
            identifier,
            "2026-01-10",
            "Chased the entry",
            realized_return=0.02,
            alpha_return=0.01,
        )
        is False
    )

    reflections = memory.reflections()
    assert len(reflections) == 1
    assert isinstance(reflections[0], ReflectionEntry)
    assert reflections[0].decision_id == identifier
    assert reflections[0].ticker == "BTC"
    assert reflections[0].realized_return == 0.02


def test_reflection_cannot_predate_its_decision() -> None:
    memory = DecisionMemory()
    identifier = _decision_id(memory, "BTC", "2026-01-10", "Buy")

    with pytest.raises(ValueError):
        memory.append_reflection(identifier, "2026-01-09", "lesson")


def test_past_context_is_point_in_time() -> None:
    memory = DecisionMemory()
    identifier = _decision_id(memory, "BTC", "2026-01-01", "Buy the breakout")
    memory.append_reflection(identifier, "2026-01-10", "Breakout failed", realized_return=-0.03)

    assert memory.past_context("BTC", as_of="2026-01-05") == ""
    context = memory.past_context("BTC", as_of="2026-01-10")
    assert "Past analyses of BTC" in context
    assert "Buy the breakout" in context
    assert "Breakout failed" in context
    assert "resolved 2026-01-10" in context
    assert "-3.00%" in context


def test_past_context_accepts_date_objects_and_limits_sections() -> None:
    memory = DecisionMemory()
    btc_one = _decision_id(memory, "BTC", "2026-01-01", "BTC idea one")
    btc_two = _decision_id(memory, "BTC", "2026-01-02", "BTC idea two")
    eth = _decision_id(memory, "ETH", "2026-01-03", "ETH idea")
    memory.append_reflection(btc_one, "2026-01-05", "lesson one")
    memory.append_reflection(btc_two, "2026-01-06", "lesson two")
    memory.append_reflection(eth, "2026-01-07", "cross lesson")

    context = memory.past_context("BTC", as_of=date(2026, 1, 7), n_same=1, n_cross=1)

    assert "BTC idea two" in context
    assert "BTC idea one" not in context
    assert "Recent cross-ticker lessons" in context
    assert "ETH idea" in context


def test_unresolved_decisions_are_absent_from_context() -> None:
    memory = DecisionMemory()
    _decision_id(memory, "BTC", "2026-01-01", "Pending idea")

    assert memory.past_context("BTC") == ""


def test_zero_limits_return_empty_context() -> None:
    memory = DecisionMemory()
    identifier = _decision_id(memory, "BTC", "2026-01-01", "Buy")
    memory.append_reflection(identifier, "2026-01-02", "lesson")

    assert memory.past_context("BTC", n_same=0, n_cross=0) == ""


def test_persistence_roundtrip(tmp_path) -> None:
    path = tmp_path / "nested" / "memory.jsonl"
    memory = DecisionMemory(path)
    identifier = _decision_id(memory, "BTC", "2026-01-01", "Buy")
    memory.append_reflection(identifier, "2026-01-02", "lesson", realized_return=0.01)

    reloaded = DecisionMemory(path)

    assert reloaded.entries() == memory.entries()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["kind"] in {"decision", "reflection"} for line in lines)


def test_persistence_is_idempotent_across_instances(tmp_path) -> None:
    path = tmp_path / "memory.jsonl"
    first = DecisionMemory(path)
    first.append_decision("BTC", "2026-01-01", "Buy")

    second = DecisionMemory(path)
    assert second.append_decision("BTC", "2026-01-01", "Buy") is False
    assert len(second.entries()) == 1


def test_corrupt_file_raises_format_error(tmp_path) -> None:
    path = tmp_path / "memory.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")

    with pytest.raises(MemoryFormatError):
        DecisionMemory(path)


def test_unknown_kind_and_missing_field_raise_format_error(tmp_path) -> None:
    path = tmp_path / "memory.jsonl"
    path.write_text('{"kind": "other"}\n', encoding="utf-8")
    with pytest.raises(MemoryFormatError):
        DecisionMemory(path)

    path.write_text('{"kind": "decision", "entry_id": "x"}\n', encoding="utf-8")
    with pytest.raises(MemoryFormatError):
        DecisionMemory(path)


def test_input_validation() -> None:
    memory = DecisionMemory()

    with pytest.raises(ValueError):
        memory.append_decision("", "2026-01-01", "Buy")
    with pytest.raises(ValueError):
        memory.append_decision("BTC", "2026-01-01", "   ")
    with pytest.raises(ValueError):
        memory.append_decision("BTC", "not-a-date", "Buy")
    with pytest.raises(ValueError):
        memory.past_context("BTC", n_same=-1)

    identifier = _decision_id(memory, "BTC", "2026-01-01", "Buy")
    with pytest.raises(ValueError):
        memory.append_reflection(identifier, "2026-01-02", "   ")


def test_entries_are_immutable_snapshots() -> None:
    memory = DecisionMemory()
    _decision_id(memory, "BTC", "2026-01-01", "Buy")

    snapshot = memory.entries()
    assert isinstance(snapshot, tuple)
    with pytest.raises(FrozenInstanceError):
        snapshot[0].decision = "changed"  # type: ignore[misc]


def test_reflection_id_is_deterministic() -> None:
    assert reflection_id("abc", "2026-01-01", "lesson") == reflection_id(
        "abc", "2026-01-01", "lesson"
    )
    assert reflection_id("abc", "2026-01-01", "lesson") != reflection_id(
        "abc", "2026-01-02", "lesson"
    )
