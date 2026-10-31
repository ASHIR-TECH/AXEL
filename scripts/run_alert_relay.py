"""Run the AXEL alert relay (Alertmanager webhook -> PagerDuty/Telegram/webhook)."""

from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer

from axel.core.config import settings
from axel.core.logging import logger, setup_logging
from axel.observability.alert_relay import DispatchResult, dispatch_alerts, make_handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AXEL alert relay")
    parser.add_argument("--host", default=settings.alert_relay_host)
    parser.add_argument("--port", type=int, default=settings.alert_relay_port)
    args = parser.parse_args()

    setup_logging(
        settings.log_level,
        log_file=settings.log_file,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        force=True,
    )

    pagerduty_key = (
        settings.pagerduty_integration_key.get_secret_value()
        if settings.pagerduty_integration_key
        else ""
    )
    telegram_token = (
        settings.telegram_bot_token.get_secret_value()
        if settings.telegram_bot_token
        else ""
    )
    telegram_chat_id = settings.telegram_operator_chat_id or ""

    def dispatcher(payload: dict[str, object]) -> DispatchResult:
        return dispatch_alerts(
            payload,
            pagerduty_routing_key=pagerduty_key,
            telegram_bot_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
            webhook_url=settings.alert_webhook_url,
            dry_run=settings.alert_relay_dry_run,
        )

    server = ThreadingHTTPServer((args.host, args.port), make_handler(dispatcher))
    server.daemon_threads = True
    logger.info(
        "AXEL alert relay listening",
        extra={
            "host": args.host,
            "port": args.port,
            "pagerduty": bool(pagerduty_key),
            "telegram": bool(telegram_token and telegram_chat_id),
            "webhook": bool(settings.alert_webhook_url),
            "dry_run": settings.alert_relay_dry_run,
        },
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("AXEL alert relay stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
