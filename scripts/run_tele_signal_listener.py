"""
Run the Telegram signal listener for AXEL.

Modes:
  --demo      replay canned Telegram signals through the pipeline (no live creds needed).
  (default)   listen on real Telegram groups using the operator's own account (Telethon).

Live mode env (or .env):
  TELEGRAM_API_ID / TELEGRAM_API_HASH    (your Telegram developer credentials)
  TELEGRAM_SESSION                       (session file name, default axel_tele_signal)
  SIGNAL_CHATS                           comma-separated group usernames/ids to monitor
  SIGNAL_ALLOWED_USER_IDS                optional comma-separated senders to trust
  SIGNAL_MIN_TRADE_CONFIDENCE            default 0.50

Install:  .venv/bin/pip install 'axel[telegram]'
Run:      .venv/bin/python scripts/run_tele_signal_listener.py --demo
"""

import argparse
import asyncio
import sys

from axel.signals.config import SignalsConfig
from axel.signals.pipeline import TeleSignalPipeline
from axel.signals.source import DemoTeleSource, TelegramMessage, TelethonGroupSource


def _print_result(message: TelegramMessage, result) -> None:
    print(f"\n[{message.chat}] {message.text[:80]}")
    print(f"  -> {result.kind.value:>14} | {result.reason} | persisted={result.persisted}")
    if result.decision is not None and getattr(result.decision, "approved", None) is not None:
        print(
            f"     risk: {result.decision.verdict.value}, qty={result.decision.approved_qty}, "
            f"notional=${result.decision.approved_notional_usd:,.2f}"
        )


def run_demo(min_conf: float) -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    pipeline = TeleSignalPipeline(min_trade_confidence=min_conf)
    source = DemoTeleSource()
    print("AXEL Telegram signal ingest (DEMO)")
    print("=" * 70)
    for message in source.read():
        _print_result(message, pipeline.ingest(message))
    print("\nDemo complete. Signals above were recorded via the same deterministic gates")
    print("that protect live trading: classify -> parse -> risk engine.")
    return 0


async def run_live(config: SignalsConfig, min_conf: float) -> int:
    if config.api_id is None or config.api_hash is None:
        print("Missing TELEGRAM_API_ID/TELEGRAM_API_HASH for live mode; use --demo instead.")
        return 2
    pipeline = TeleSignalPipeline(min_trade_confidence=min_conf)
    source = TelethonGroupSource(
        api_id=config.api_id,
        api_hash=config.api_hash,
        session_name=config.session_name,
        chats=config.chats,
        allowed_user_ids=config.allowed_user_ids,
    )

    async def _on_message(message: TelegramMessage) -> None:
        _print_result(message, pipeline.ingest(message))

    print(f"Listening on chats: {', '.join(config.chats) or '(SIGNAL_CHATS empty)'}")
    await source.run(_on_message)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AXEL Telegram signal listener")
    parser.add_argument("--demo", action="store_true", help="replay canned demo signals")
    parser.add_argument("--min-confidence", type=float, default=None, help="min trade confidence (default from env: 0.50)")
    args = parser.parse_args()

    config = SignalsConfig.from_env()
    min_conf = args.min_confidence if args.min_confidence is not None else config.min_trade_confidence

    if args.demo:
        return run_demo(min_conf)
    return asyncio.run(run_live(config, min_conf))


if __name__ == "__main__":
    raise SystemExit(main())