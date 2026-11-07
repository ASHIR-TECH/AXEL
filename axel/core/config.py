"""
Configuration management for AXEL using Pydantic Settings.
Enforces strict validation and safeguards against unintended live trading.
"""

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    def is_live_permitted(self) -> bool:
        """Returns True only when all explicit live confirmations are met."""
        return (
            self.environment == "live"
            and self.live_trading_confirmed
            and not self.alpaca_paper
        )


settings = AxelSettings()
