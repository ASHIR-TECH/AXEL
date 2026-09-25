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

    # Hardcoded Risk Defaults (Read-only baseline)
    global_kill_pct: float = 0.10          # 10% account max drawdown
    section_daily_stop_pct: float = 0.03   # 3% section daily stop
    kelly_fraction: float = 0.25           # Fractional Kelly cap
    max_position_pct: float = 0.05         # 5% max notional per trade
    max_per_trade_risk_pct: float = 0.01   # 1% risk-at-stop cap

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
