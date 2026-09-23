"""Read-only, bounded HTTP clients for data sources used in paper research."""

from datetime import UTC, datetime

import httpx

from axel.data.models import Bar


class AlpacaMarketData:
    base_url = "https://data.alpaca.markets/v2"

    def __init__(self, api_key: str, secret_key: str, client: httpx.Client | None = None) -> None:
        if not api_key or not secret_key:
            raise ValueError("Alpaca market-data credentials are required")
        self._headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}
        self._client = client or httpx.Client(timeout=20)

    def bars(self, symbol: str, start: datetime, end: datetime, timeframe: str = "1Day") -> list[Bar]:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("start/end must be ordered timezone-aware datetimes")
        response = self._client.get(f"{self.base_url}/stocks/bars", headers=self._headers, params={
            "symbols": symbol.upper(), "start": start.astimezone(UTC).isoformat(),
            "end": end.astimezone(UTC).isoformat(), "timeframe": timeframe, "feed": "iex",
        })
        response.raise_for_status()
        records = response.json().get("bars", {}).get(symbol.upper(), [])
        return [Bar(symbol=symbol.upper(), timestamp=datetime.fromisoformat(item["t"]),
                    open=float(item["o"]), high=float(item["h"]), low=float(item["l"]),
                    close=float(item["c"]), volume=float(item["v"]), timeframe=timeframe) for item in records]


class FredData:
    endpoint = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        if not api_key:
            raise ValueError("FRED_API_KEY is required")
        self._api_key, self._client = api_key, client or httpx.Client(timeout=20)

    def observations(self, series_id: str, *, realtime_as_of: str) -> list[dict[str, str]]:
        response = self._client.get(self.endpoint, params={"series_id": series_id, "api_key": self._api_key,
            "file_type": "json", "realtime_start": realtime_as_of, "realtime_end": realtime_as_of})
        response.raise_for_status()
        return [item for item in response.json().get("observations", []) if item.get("value") != "."]


class SecEdgar:
    base_url = "https://data.sec.gov/submissions"

    def __init__(self, user_agent: str, client: httpx.Client | None = None) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT must identify a contact email")
        self._headers, self._client = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}, client or httpx.Client(timeout=20)

    def submissions(self, cik: str) -> dict[str, object]:
        normalized = cik.zfill(10)
        if not normalized.isdigit():
            raise ValueError("CIK must be numeric")
        response = self._client.get(f"{self.base_url}/CIK{normalized}.json", headers=self._headers)
        response.raise_for_status()
        return response.json()
