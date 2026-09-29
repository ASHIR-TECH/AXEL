"""
Message sources for the Telegram signal pipeline.

- DemoTeleSource: canned messages for tests and the --demo run mode.
- TelethonGroupSource: listens to real Telegram groups/chats through the
  operator's OWN Telegram account (Telethon). Telethon is imported lazily so
  the rest of AXEL never requires it.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TelegramMessage:
    chat: str
    text: str
    sender_id: int | None = None


DEMO_MESSAGES: list[TelegramMessage] = [
    TelegramMessage(
        chat="@demo_betting",
        text="Booking Code: 4XK2Q9 win odds 1.85, stake 20",
    ),
    TelegramMessage(
        chat="@demo_betting",
        text="ACCUMULATOR: E7MX2A + code 9FHW33, sure banker today",
    ),
    TelegramMessage(
        chat="@demo_crypto",
        text="BUY BTCUSDT entry 63000 stop loss 61800 take profit 64500 \u2014 high confidence breakout",
    ),
    TelegramMessage(
        chat="@demo_crypto",
        text="SHORT ETHUSDT entry 3400 sl 3500 tp 3200, nice little scalp",
    ),
    TelegramMessage(
        chat="@demo_forex",
        text="EURUSD buy entry 1.0850 target 1.0920 stop 1.0810",
    ),
    TelegramMessage(
        chat="@demo_stocks",
        text="$AAPL buy entry 185 target 200 stop 178.5 \u2014 breakout confirmed",
    ),
    TelegramMessage(chat="@demo_stocks", text="join our vip group for more winners"),
    TelegramMessage(chat="@demo_crypto", text="gm everyone, how is the market today?"),
]


class DemoTeleSource:
    """Replays a fixed set of Telegram messages (tests / --demo)."""

    def __init__(self, messages: list[TelegramMessage] | None = None) -> None:
        self._messages = messages if messages is not None else DEMO_MESSAGES

    def read(self) -> list[TelegramMessage]:
        return list(self._messages)


class TelethonGroupSource:
    """Async listener that monitors Telegram groups via the operator's account.

    Requires `telethon` (project extra: `pip install 'axel[telegram]'`),
    plus TELEGRAM_API_ID / TELEGRAM_API_HASH and a session. Messages are
    filtered by configured chats and optional allowed sender IDs.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str,
        chats: tuple[str, ...] = (),
        allowed_user_ids: frozenset[int] = frozenset(),
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.chats = chats
        self.allowed_user_ids = allowed_user_ids

    async def run(self, on_message: Callable[[TelegramMessage], Awaitable[Any] | Any]) -> None:
        if not self.chats:
            raise RuntimeError("SIGNAL_CHATS is empty; configure at least one Telegram group.")
        try:
            from telethon import TelegramClient, events
        except ImportError as exc:
            raise RuntimeError(
                "telethon is not installed. Run: .venv/bin/pip install 'axel[telegram]'"
            ) from exc

        if not self.chats:
            raise RuntimeError("No Telegram chats configured (SIGNAL_CHATS).")

        async def _handler(event: events.NewMessage.Event) -> None:
            message = event.message
            text = (message.raw_text or "").strip()
            if not text:
                return
            sender = await event.get_sender()
            sender_id = getattr(sender, "id", None)
            if self.allowed_user_ids and sender_id not in self.allowed_user_ids:
                return
            entry = TelegramMessage(chat=event.chat_id or "", text=text, sender_id=sender_id)
            result = on_message(entry)
            if asyncio.iscoroutine(result):
                await result

        client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        await client.start()
        client.add_event_handler(_handler, events.NewMessage(chats=self.chats))
        await client.run_until_disconnected()