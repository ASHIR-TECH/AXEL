"""Configuration for the Telegram signal ingestion subsystem."""

import os
from dataclasses import dataclass


def _csv(name: str) -> tuple[str, ...]:
    return tuple(token.strip() for token in os.getenv(name, "").split(",") if token.strip())


def _user_ids(name: str) -> frozenset[int]:
    ids: set[int] = set()
    for token in os.getenv(name, "").split(","):
        token = token.strip()
        if token.isdigit():
            ids.add(int(token))
    return frozenset(ids)


@dataclass(frozen=True)
class SignalsConfig:
    """Telegram group/source settings, loaded from the environment (SIGNAL_* keys)."""

    chats: tuple[str, ...] = ()
    allowed_user_ids: frozenset[int] = frozenset()
    min_trade_confidence: float = 0.50
    api_id: int | None = None
    api_hash: str | None = None
    session_name: str = "axel_tele_signal"

    @classmethod
    def from_env(cls) -> "SignalsConfig":
        api_id_raw = os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID") or ""
        api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH") or ""
        try:
            min_conf = float(os.getenv("SIGNAL_MIN_TRADE_CONFIDENCE", "0.50"))
        except ValueError:
            min_conf = 0.50
        return cls(
            chats=_csv("SIGNAL_CHATS"),
            allowed_user_ids=_user_ids("SIGNAL_ALLOWED_USER_IDS"),
            min_trade_confidence=min_conf,
            api_id=int(api_id_raw) if api_id_raw.isdigit() else None,
            api_hash=api_hash or None,
            session_name=os.getenv("TELEGRAM_SESSION") or "axel_tele_signal",
        )