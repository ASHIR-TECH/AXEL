"""Tests for the live Alpaca paper soak status probe.

These tests exercise the order-summary logic of :func:`scripts.paper_live.main`
without network access.
"""

from unittest import mock

from scripts.paper_live import main


def test_main_counts_fills_and_working_orders(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fake-key")
    orders = [
        {"symbol": "AAPL", "status": "filled"},
        {"symbol": "MSFT", "status": "partially_filled"},
        {"symbol": "TSLA", "status": "new"},
        {"symbol": "NVDA", "status": "canceled"},
    ]
    client = mock.Mock()
    client.get.return_value.json.return_value = orders
    with mock.patch("scripts.paper_live.httpx", client):
        rc = main()

    assert rc == 0
    assert client.get.call_count == 1


def test_main_alarms_on_too_many_working_orders(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fake-key")
    orders = [{"symbol": f"S{i}", "status": "new"} for i in range(21)]
    client = mock.Mock()
    client.get.return_value.json.return_value = orders
    with mock.patch("scripts.paper_live.httpx", client):
        rc = main()

    assert rc == 1


def test_main_fails_cleanly_when_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "fake-key")
    client = mock.Mock()
    client.get.side_effect = ConnectionError("no network")
    with mock.patch("scripts.paper_live.httpx", client):
        rc = main()

    assert rc == 2
