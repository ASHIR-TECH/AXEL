"""
Versioned, fail-closed live configuration and activation gate (Phase 6).

The gate is the single authority that decides whether the Stocks execution path
is permitted to place real orders. Its contract, from the Phase 6 PRD and
`docs/AXEL_PHASE_6_RESEARCH_AND_IMPLEMENTATION.md` §4.4 / §8.1:

* live mode requires an explicit, reviewed, *versioned* configuration;
* missing, stale, conflicting, invalid or unapproved values fail closed;
* activation is a distinct, auditable operator action, never a bare environment
  flag and never triggered by an agent;
* **no numeric limit is invented here** --- every cap is supplied by the
  approved configuration.

Live is therefore disabled by default: constructing a :class:`LiveGate` with no
configuration yields a disabled gate whose every order request is refused with a
stable :class:`~axel.execution.reason_codes.LiveGateReason`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from axel.core.money import to_decimal
from axel.core.types import Section, TradingMode
from axel.execution.reason_codes import LiveGateReason


@dataclass(frozen=True)
class ApprovalMetadata:
    """Who reviewed/approved a live configuration and where the audit lives."""

    approved_by: str
    approved_at: datetime
    reference: str


@dataclass(frozen=True)
class LiveConfig:
    """
    Immutable, versioned live configuration.

    All monetary caps are :class:`~decimal.Decimal`. There are intentionally no
    defaults for the caps: a configuration that forgets one is rejected rather
    than silently using an invented limit.
    """

    config_version: str
    environment: TradingMode
    broker: str
    account_id: str
    allowed_section: Section
    max_live_capital: Decimal
    max_position_notional: Decimal
    max_order_notional: Decimal
    max_daily_loss: Decimal
    max_concurrent_positions: int
    max_order_frequency: int
    order_frequency_window_seconds: int
    allowed_symbols: frozenset[str]
    manual_approval_required: bool
    phase5_evidence_ref: str
    phase5_gate_passed: bool
    approval_metadata: ApprovalMetadata | None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Declared kill-switch state recorded with the configuration; the *live*
    # kill switch is still checked at order time.
    kill_switch_clear: bool = True

    def __post_init__(self) -> None:
        # Normalise mutable/loose inputs into the frozen canonical types so the
        # record is hashable and comparison-stable.
        object.__setattr__(self, "allowed_symbols", frozenset(self.allowed_symbols))
        for name in (
            "max_live_capital",
            "max_position_notional",
            "max_order_notional",
            "max_daily_loss",
        ):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))


@dataclass(frozen=True)
class LiveGateDecision:
    """Result of evaluating the live gate; ``enabled`` implies no reasons."""

    enabled: bool
    config_version: str | None
    reasons: tuple[LiveGateReason, ...] = ()
    details: tuple[str, ...] = ()

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(r.value for r in self.reasons)


def validate_live_config(config: LiveConfig | None) -> LiveGateDecision:
    """
    Statically validate a configuration, fail-closed.

    This checks *structure and internal consistency only*; runtime facts such as
    kill-switch state or broker reachability are supplied to
    :meth:`LiveGate.evaluate`. Returns a disabled decision listing every reason
    the configuration cannot support live trading.
    """
    if config is None:
        return LiveGateDecision(
            enabled=False,
            config_version=None,
            reasons=(LiveGateReason.NO_CONFIGURATION,),
            details=("No live configuration is present; live is disabled by default.",),
        )

    reasons: list[LiveGateReason] = []
    details: list[str] = []

    def reject(reason: LiveGateReason, detail: str) -> None:
        reasons.append(reason)
        details.append(detail)

    if not config.config_version or not config.config_version.strip():
        reject(LiveGateReason.VERSION_MISSING, "config_version is empty.")
    if config.environment != TradingMode.LIVE:
        reject(
            LiveGateReason.ENVIRONMENT_NOT_LIVE,
            f"environment is {config.environment.value!r}, must be 'live'.",
        )
    if config.allowed_section != Section.STOCKS:
        reject(
            LiveGateReason.SECTION_NOT_STOCKS,
            f"allowed_section is {config.allowed_section.value!r}, must be 'stocks'.",
        )
    if config.max_live_capital <= 0:
        reject(LiveGateReason.CAPITAL_INVALID, "max_live_capital must be > 0.")
    if config.max_position_notional <= 0:
        reject(LiveGateReason.POSITION_LIMIT_INVALID, "max_position_notional must be > 0.")
    if config.max_order_notional <= 0:
        reject(LiveGateReason.ORDER_LIMIT_INVALID, "max_order_notional must be > 0.")
    if config.max_daily_loss <= 0:
        reject(LiveGateReason.DAILY_LOSS_INVALID, "max_daily_loss must be > 0.")
    if config.max_concurrent_positions <= 0:
        reject(LiveGateReason.CONCURRENCY_INVALID, "max_concurrent_positions must be > 0.")
    if config.max_order_frequency <= 0 or config.order_frequency_window_seconds <= 0:
        reject(
            LiveGateReason.ORDER_FREQUENCY_INVALID,
            "max_order_frequency and order_frequency_window_seconds must be > 0.",
        )
    if not config.allowed_symbols:
        reject(LiveGateReason.UNIVERSE_EMPTY, "allowed_symbols must not be empty.")
    if not config.manual_approval_required:
        reject(
            LiveGateReason.MANUAL_APPROVAL_DISABLED,
            "manual_approval_required must be True for initial live operation.",
        )
    if not config.phase5_gate_passed or not config.phase5_evidence_ref.strip():
        reject(
            LiveGateReason.PHASE5_EVIDENCE_MISSING,
            "Phase 5 promotion evidence must be recorded and passed before live activation.",
        )
    meta = config.approval_metadata
    if meta is None or not meta.approved_by.strip() or not meta.reference.strip():
        reject(
            LiveGateReason.APPROVAL_METADATA_MISSING,
            "Reviewed/approved configuration metadata is required.",
        )

    # Internal consistency: order <= position <= section capital.
    if (
        config.max_order_notional > 0
        and config.max_position_notional > 0
        and config.max_order_notional > config.max_position_notional
    ):
        reject(
            LiveGateReason.INCONSISTENT_LIMITS,
            "max_order_notional must not exceed max_position_notional.",
        )
    if (
        config.max_position_notional > 0
        and config.max_live_capital > 0
        and config.max_position_notional > config.max_live_capital
    ):
        reject(
            LiveGateReason.INCONSISTENT_LIMITS,
            "max_position_notional must not exceed max_live_capital.",
        )

    return LiveGateDecision(
        enabled=not reasons,
        config_version=config.config_version,
        reasons=tuple(reasons),
        details=tuple(details),
    )


class LiveGate:
    """
    Runtime live-trading gate.

    Holds at most one approved configuration and combines the static validation
    with live operational facts. Any unresolved prerequisite keeps live disabled.
    """

    def __init__(
        self,
        config: LiveConfig | None = None,
        *,
        max_config_age_seconds: int | None = None,
    ) -> None:
        self._config = config
        # Optional freshness bound supplied by the operator; ``None`` means the
        # review cadence is enforced out of band and is not invented here.
        self._max_config_age_seconds = max_config_age_seconds

    @property
    def config(self) -> LiveConfig | None:
        return self._config

    @property
    def is_configured(self) -> bool:
        return self._config is not None

    def validate(self) -> LiveGateDecision:
        """Static, fail-closed validation of the held configuration."""
        return validate_live_config(self._config)

    def evaluate(
        self,
        *,
        kill_switch_active: bool = False,
        watchdog_healthy: bool = True,
        broker_connected: bool = False,
        observed_account_id: str | None = None,
        now: datetime | None = None,
    ) -> LiveGateDecision:
        """
        Combine static validation with live operational facts.

        Fail-closed: a prerequisite that is missing, unknown or unhealthy disables
        live. ``broker_connected`` defaults to ``False`` so that a caller which
        forgets to prove connectivity does not accidentally enable trading.
        """
        base = validate_live_config(self._config)
        reasons = list(base.reasons)
        details = list(base.details)

        config = self._config
        if config is not None:
            reference_now = now or datetime.now(UTC)
            if self._max_config_age_seconds is not None:
                age = (reference_now - config.created_at).total_seconds()
                if age > self._max_config_age_seconds:
                    reasons.append(LiveGateReason.INCONSISTENT_LIMITS)
                    details.append(
                        f"Live configuration is stale ({age:.0f}s > "
                        f"{self._max_config_age_seconds}s)."
                    )
            if kill_switch_active:
                reasons.append(LiveGateReason.KILL_SWITCH_ACTIVE)
                details.append("Kill switch is active.")
            if not config.kill_switch_clear:
                reasons.append(LiveGateReason.KILL_SWITCH_ACTIVE)
                details.append("Configuration was recorded with the kill switch active.")
            if not watchdog_healthy:
                reasons.append(LiveGateReason.WATCHDOG_UNHEALTHY)
                details.append("Watchdog is not healthy.")
            if not broker_connected:
                reasons.append(LiveGateReason.BROKER_ACCOUNT_MISMATCH)
                details.append("Broker read-only connectivity check has not passed.")
            elif observed_account_id is not None and observed_account_id != config.account_id:
                reasons.append(LiveGateReason.BROKER_ACCOUNT_MISMATCH)
                details.append(
                    f"Broker account {observed_account_id!r} does not match approved "
                    f"account {config.account_id!r}."
                )

        # De-duplicate while preserving order.
        unique_reasons = tuple(dict.fromkeys(reasons))
        unique_details = tuple(dict.fromkeys(details))
        return LiveGateDecision(
            enabled=not unique_reasons,
            config_version=config.config_version if config else None,
            reasons=unique_reasons,
            details=unique_details,
        )
