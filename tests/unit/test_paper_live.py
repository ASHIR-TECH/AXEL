"""Tests for the live Alpaca paper soak status probe.

These tests exercise the state logic of :func:`scripts.paper_live.main` without
making real network calls. The production probe itself requires egress to Alpaca.
"""

from unittest import mock

from scripts.paper_live import main


def test_main_counts_fills_and_defers(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fake-key")
    positions = [
        {"symbol": "AAPL", "status": "filled"},
        {"symbol": "MSFT", "status": "partially_filled"},
        {"symbol": "TSLA", "status": "new"},
        {"symbol": "NVDA", "status": "canceled"},
    ]
    session = mock.Mock()
    session.get.return_value.json.return_value = positions
    with mock.patch("scripts.paper_live.requests", session):
        rc = main()

    assert rc == 0
    assert session.get.call_count == 2


def test_main_alarms_on_too_many_unfilled(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fake-key")
    positions = [{"symbol": f"S{i}", "status": "new", "qty": 0.0} for i in range(21)]
    session = mock.Mock()
    session.get.return_value.json.return_value = positions
    with mock.patch("scripts.paper_live.requests", session):
        rc = main()

    assert rc == 1


def test_main_crashes_gracefully_when_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fake-key")
    session = mock.Mock()
    session.get.side_effect = ConnectionError("no network")
    with mock.patch("scripts.paper_live.requests", session):
        rc = main()

    assert rc == 2
