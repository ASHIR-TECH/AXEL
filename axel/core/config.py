"""
Configuration management for AXEL using Pydantic Settings.
Enforces strict validation and safeguards against unintended live trading.
"""

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _collect_pairs(
    *,
    primary: SecretStr | None,
    primary_secret: SecretStr | None,
    secondary: SecretStr | None,
    secondary_secret: SecretStr | None,
    combined: SecretStr | None,
    combined_label: str,
) -> list[tuple[str, str]]:
    """Merge optional key pairs, skipping incomplete ones and de-duplicating.

    The combined form is ``key:secret,key:secret``. A malformed entry is a
    configuration error, not something to silently drop.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(key: SecretStr | None, secret: SecretStr | None) -> None:
        if key is None or secret is None:
            return
        key_id = key.get_secret_value().strip()
        key_secret = secret.get_secret_value().strip()
        if not key_id or not key_secret or key_id in seen:
            return
        seen.add(key_id)
        pairs.append((key_id, key_secret))

    add(primary, primary_secret)
    add(secondary, secondary_secret)
    if combined is not None:
        for entry in combined.get_secret_value().split(","):
            entry = entry.strip()
            if not entry:
                continue
            key_id, separator, key_secret = entry.partition(":")
            if not separator or not key_id.strip() or not key_secret.strip():
                raise ValueError(
                    f"{combined_label} entries must be 'key:secret' pairs separated by commas"
                )
            if key_id.strip() in seen:
                continue
            seen.add(key_id.strip())
            pairs.append((key_id.strip(), key_secret.strip()))
    return pairs


class AxelSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Execution Mode
    environment: Literal["paper", "live"] = "paper"
    live_trading_confirmed: bool = False
    log_level: str = "INFO"

    # Logging. Empty LOG_FILE keeps stdout-only output (Docker/journald).
    log_file: str = ""
    log_max_bytes: int = 10_000_000
    log_backup_count: int = 5

    # Database & Storage
    database_url: str = "sqlite:///./axel.db"
    redis_url: str = "redis://localhost:6379/0"

    # Broker: Alpaca (Equities)
    alpaca_api_key: SecretStr | None = None
    alpaca_secret_key: SecretStr | None = None
    # Additional read-only market-data account. Rotating accounts is a
    # data-side concern only; execution still uses the primary pair.
    alpaca_api_key_2: SecretStr | None = None
    alpaca_secret_key_2: SecretStr | None = None
    # Optional catch-all: comma-separated ``key:secret`` pairs.
    alpaca_api_keys: SecretStr | None = None
    alpaca_key_strategy: Literal["failover", "round_robin", "random"] = "failover"
    alpaca_key_cooldown_seconds: float = 60.0
    alpaca_paper: bool = True
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    # Human-In-The-Loop & Comms
    telegram_bot_token: SecretStr | None = None
    telegram_operator_chat_id: str | None = None
    operator_rearm_secret: SecretStr = Field(default_factory=lambda: SecretStr("axel-dev-rearm-secret"))

    # AI Model Providers (Used ONLY by Agent service)
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    llm_provider: Literal["none", "groq", "qwen"] = "none"
    groq_api_key: SecretStr | None = None
    qwen_api_key: SecretStr | None = None
    # Override only for an explicitly selected provider; Qwen is region/workspace-specific.
    llm_base_url: str = ""
    llm_model: str = ""
    llm_hourly_budget_usd: float = 0.25
    llm_daily_budget_usd: float = 1.00

    # Data providers. These are read-only credentials; execution stays isolated.
    fred_api_key: SecretStr | None = None
    sec_user_agent: str | None = None
    massive_api_key: SecretStr | None = None
    massive_base_url: str = "https://api.massive.com"
    # Equibles supports the same rotation policy as Alpaca's data keys.
    equibles_api_key: SecretStr | None = None
    equibles_api_key_2: SecretStr | None = None
    equibles_api_keys: SecretStr | None = None
    equibles_key_strategy: Literal["failover", "round_robin", "random"] = "failover"
    equibles_key_cooldown_seconds: float = 60.0
    equibles_base_url: str = "https://api.equibles.com/v1"
    # Twelve Data: daily/intraday OHLCV via the ``apikey`` query parameter.
    twelve_data_api_key: SecretStr | None = None
    twelve_data_base_url: str = "https://api.twelvedata.com"
    # Finnhub: company news and quotes via the ``X-Finnhub-Token`` header.
    # Historical candles are a paid-tier endpoint; the adapter reports that
    # clearly instead of fabricating bars.
    finnhub_api_key: SecretStr | None = None
    finnhub_base_url: str = "https://finnhub.io/api/v1"

    # Monitoring / alerting. All defaults keep the exporter and relay bound to
    # loopback when run outside Docker; Compose overrides the bind address.
    metrics_host: str = "127.0.0.1"
    metrics_port: int = 9108
    alert_relay_host: str = "127.0.0.1"
    alert_relay_port: int = 9110
    # These are shared with the trading process so the exporter can read the
    # kill-switch and halt state written by the core.
    killswitch_state_file: str = ".axel_killswitch.json"
    halt_state_file: str = ".axel_halt.json"
    # Pager / notification routing. Empty values disable that channel.
    pagerduty_integration_key: SecretStr | None = None
    alert_webhook_url: str = ""
    alert_relay_dry_run: bool = False
    # Durable operational event log (API failures, rejected orders, quota
    # exhaustion). The exporter reads it to expose alertable metrics.
    persist_operational_events: bool = True
    operational_event_retention_days: int = 30
    # Retention for the high-volume tables. Defaults are long enough to cover
    # a full 56-day paper soak plus analysis, while still bounding growth.
    audit_log_retention_days: int = 90
    signal_retention_days: int = 90
    market_data_retention_days: int = 365
    equity_snapshot_retention_days: int = 730
    retention_batch_size: int = 5_000

    # Hardcoded Risk Defaults (Read-only baseline)
    global_kill_pct: float = 0.10          # 10% account max drawdown
    section_daily_stop_pct: float = 0.03   # 3% section daily stop
    kelly_fraction: float = 0.25           # Fractional Kelly cap
    max_position_pct: float = 0.05         # 5% max notional per trade
    max_per_trade_risk_pct: float = 0.01   # 1% risk-at-stop cap

    # Live dashboard API. The API is read-only; mutations stay in the
    # authenticated execution/approval services. Leave the token empty for
    # local development and set it in production.
    dashboard_api_token: SecretStr | None = None
    dashboard_cors_origins: str = "*"
    dashboard_poll_seconds: float = 1.0
    dashboard_stream_heartbeat_seconds: float = 5.0
    llm_monthly_budget_usd: float = 50.0

    # Deterministic paper runtime. The workers only run when environment is
    # "paper"; the seed makes synthetic bars reproducible across restarts.
    # "alpaca" reads real read-only IEX bars for the stocks section and narrows
    # the runtime to stocks; "synthetic" is the offline default.
    paper_data_source: Literal["synthetic", "alpaca"] = "synthetic"
    # "alpaca" sends approved orders to the Alpaca paper endpoint so fills and
    # slippage are real; "internal" keeps the deterministic offline fill.
    paper_execution: Literal["internal", "alpaca"] = "internal"
    # Cash the paper ledger starts from. Keep it equal to the broker paper
    # account's cash so local NAV and broker equity track each other.
    paper_starting_equity: float = 10_000.0
    paper_runtime_interval_seconds: float = 5.0
    paper_runtime_seed: int = 7
    paper_runtime_signal_ttl_seconds: int = 900
    paper_runtime_proposal_cooldown_seconds: int = 30

    @model_validator(mode="after")
    def validate_safety_locks(self) -> "AxelSettings":
        """
        Double-lock safety check:
        Live trading requires BOTH environment='live' AND live_trading_confirmed=True.
        """
        if self.environment == "live" and not self.live_trading_confirmed:
            raise ValueError(
                "CRITICAL SAFETY LOCK: environment='live' is set but "
                "LIVE_TRADING_CONFIRMED is not True. Execution refused."
            )
        if self.environment == "paper" and not self.alpaca_paper:
            # Fall back safely
            object.__setattr__(self, "alpaca_paper", True)
        return self

    def alpaca_credentials(self) -> list[tuple[str, str]]:
        """Return every complete, de-duplicated Alpaca credential pair.

        The primary pair is first, then ``ALPACA_API_KEY_2`` and finally any
        pairs in ``ALPACA_API_KEYS``. Incomplete pairs are skipped so an
        optional second account cannot prevent the first from starting.
        """
        return _collect_pairs(
            primary=self.alpaca_api_key,
            primary_secret=self.alpaca_secret_key,
            secondary=self.alpaca_api_key_2,
            secondary_secret=self.alpaca_secret_key_2,
            combined=self.alpaca_api_keys,
            combined_label="ALPACA_API_KEYS",
        )

    def equibles_credentials(self) -> list[tuple[str, str]]:
        """Return every complete Equibles key, de-duplicated.

        Equibles keys have no separate secret, so each secret slot repeats the
        key. This keeps the pool interface identical for both providers.
        """
        return _collect_pairs(
            primary=self.equibles_api_key,
            primary_secret=self.equibles_api_key,
            secondary=self.equibles_api_key_2,
            secondary_secret=self.equibles_api_key_2,
            combined=self.equibles_api_keys,
            combined_label="EQUIBLES_API_KEYS",
        )

    def is_live_permitted(self) -> bool:
        """Returns True only when all explicit live confirmations are met."""
        return (
            self.environment == "live"
            and self.live_trading_confirmed
            and not self.alpaca_paper
        )


settings = AxelSettings()
