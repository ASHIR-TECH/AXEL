import json
import logging
from dataclasses import dataclass

import httpx

from axel.comms.alerts import SEVERITY_CRITICAL, Alert

logger = logging.getLogger("axel")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Emoji prefix by severity for fast operator scanning
_SEVERITY_PREFIX: dict[str, str] = {
    SEVERITY_CRITICAL: "🔴",
    "warning": "🟡",
    "info": "🔵",
}


def _format_message(alert: Alert) -> str:
    """Renders an Alert as a readable Telegram message (Markdown v2 safe)."""
    prefix = _SEVERITY_PREFIX.get(alert.severity, "⚪")
    lines = [
        f"{prefix} *{alert.kind.upper().replace('_', ' ')}*",
        f"`{alert.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}`",
        "",
        alert.message,
    ]
    if alert.detail:
        lines.append("")
        lines.append("```")
        lines.append(json.dumps(alert.detail, indent=2, default=str))
        lines.append("```")
    return "\n".join(lines)


@dataclass
class TelegramTransport:
    """
    Sends Alert payloads to a Telegram operator chat.

    Set dry_run=True to log the message without making an HTTP call
    (useful in tests and paper mode).
    """
    bot_token: str
    chat_id: str
    dry_run: bool = False
    timeout_seconds: float = 10.0

    def send(self, alert: Alert) -> bool:
        """
        Delivers the alert. Returns True on success, False on any transport error.
        Never raises — a failed alert must not crash the trading process.
        """
        text = _format_message(alert)

        if self.dry_run:
            logger.info(
                "TelegramTransport [DRY RUN]: would send alert.",
                extra={"kind": alert.kind, "severity": alert.severity, "alert_message": alert.message},
            )
            return True

        url = _TELEGRAM_API.format(token=self.bot_token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_notification": alert.severity != SEVERITY_CRITICAL,
        }

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                logger.info(
                    "TelegramTransport: alert delivered.",
                    extra={"kind": alert.kind, "chat_id": self.chat_id},
                )
                return True
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.error(
                "TelegramTransport: delivery failed.",
                extra={"kind": alert.kind, "error": str(exc)},
            )
            return False


def transport_from_settings() -> "TelegramTransport | None":
    """
    Builds a TelegramTransport from AXEL settings.
    Returns None if Telegram credentials are not configured (safe default).
    """
    # Import here to keep the module usable without settings loaded
    from axel.core.config import settings

    if not settings.telegram_bot_token or not settings.telegram_operator_chat_id:
        logger.info("TelegramTransport: credentials not configured; transport disabled.")
        return None

    return TelegramTransport(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        chat_id=settings.telegram_operator_chat_id,
        dry_run=(settings.environment == "paper"),
    )

"""
Telegram alert transport for AXEL.

Delivers Alert payloads to a configured operator chat via the Telegram Bot API.
This module has NO imports from axel/risk/ or axel/execution/ — it is a pure
delivery wrapper and must remain on the correct side of the architectural boundary.

Usage:
    from axel.comms.telegram import TelegramTransport
    from axel.comms.alerts import halt_alert

    transport = TelegramTransport(bot_token="...", chat_id="...")
    transport.send(halt_alert("10% drawdown breached"))
"""
