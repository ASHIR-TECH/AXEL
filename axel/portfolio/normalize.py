"""
P11-NORMALIZE: domain-aware cross-asset exposure normalization.

The normalizer preserves instrument-specific behavior instead of pretending all
positions are linear notional. Options retain delta, gamma, vega, theta, max
loss, and margin. Prediction contracts retain their full payoff schedule. FX
positions retain explicit currency legs. Crypto retains venue and custody
context through the instrument registry.

Unsupported or incomplete conversions raise typed errors. The exposure builder
records them as explicit failures and marks the snapshot incomplete; it never
drops them or treats them as zero risk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from math import isfinite

from axel.core.types import Section
from axel.portfolio.registry import InstrumentRegistry, UnknownInstrumentError
from axel.portfolio.serialization import fingerprint
from axel.portfolio.state import convert_amount, worst_quality
from axel.portfolio.types import (
    AssetClass,
    DataQuality,
    ExposureSnapshot,
    FreshnessPolicy,
    FxRate,
    InstrumentSpec,
    InstrumentType,
    NormalizationFailure,
    NormalizedExposure,
    OpenOrderRecord,
    PortfolioSnapshot,
    PositionRecord,
    SectionExposure,
    SnapshotCompleteness,
    require_aware,
    utc_now,
)

DEFAULT_NORMALIZATION_VERSION = "p11-normalize-1"


class NormalizationError(ValueError):
    """Base class for exposure normalization failures."""


class UnsupportedInstrumentError(NormalizationError):
    """Raised when no defensible exposure model exists for an instrument."""


class MissingFxRateError(NormalizationError):
    """Raised when a required currency conversion is unavailable."""


class InvalidExposureError(NormalizationError):
    """Raised when required contract-level risk data is missing or invalid."""


@dataclass(frozen=True)
class NormalizationConfig:
    normalization_version: str = DEFAULT_NORMALIZATION_VERSION
    include_open_orders: bool = True
    illiquid_threshold_days: float = 5.0
    default_liquidity_horizon_days: float | None = None
    default_participation_rate: float = 0.10

    def __post_init__(self) -> None:
        if not self.normalization_version:
            raise ValueError("normalization_version must be non-empty")
        if self.illiquid_threshold_days < 0:
            raise ValueError("illiquid_threshold_days must be non-negative")
        if self.default_liquidity_horizon_days is not None and (
            self.default_liquidity_horizon_days < 0
        ):
            raise ValueError("default_liquidity_horizon_days must be non-negative")
        if not 0.0 < self.default_participation_rate <= 1.0:
            raise ValueError("default_participation_rate must be in (0, 1]")


DEFAULT_NORMALIZATION_CONFIG = NormalizationConfig()


def _convert(
    amount: float,
    currency: str,
    base_currency: str,
    fx_rates: Sequence[FxRate],
    now: datetime,
    freshness: FreshnessPolicy,
) -> tuple[float, DataQuality]:
    result = convert_amount(
        amount,
        currency,
        base_currency,
        fx_rates,
        now,
        max_age_seconds=freshness.max_fx_age_seconds,
    )
    if not result.ok or result.amount is None:
        raise MissingFxRateError(result.reason or f"missing FX conversion for {currency}")
    return result.amount, result.quality


def _metadata_float(metadata: Mapping[str, str], key: str) -> float | None:
    raw = metadata.get(key)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise InvalidExposureError(f"metadata field {key} is not numeric: {raw!r}") from exc
    if not isfinite(value):
        raise InvalidExposureError(f"metadata field {key} is not finite")
    return value


def _liquidity_horizon(
    position: PositionRecord,
    config: NormalizationConfig,
) -> tuple[float | None, tuple[str, ...]]:
    flags: list[str] = []
    safe_rate = _metadata_float(position.metadata, "safe_execution_rate")
    if safe_rate is not None:
        if safe_rate <= 0:
            raise InvalidExposureError("safe_execution_rate must be positive")
        return abs(position.quantity) / safe_rate, ()
    adv = _metadata_float(position.metadata, "adv")
    if adv is not None:
        if adv < 0:
            raise InvalidExposureError("adv must be non-negative")
        participation = _metadata_float(position.metadata, "participation_rate")
        if participation is None:
            participation = config.default_participation_rate
        if not 0.0 < participation <= 1.0:
            raise InvalidExposureError("participation_rate must be in (0, 1]")
        safe = adv * participation
        if safe <= 0:
            flags.append("zero_safe_execution_rate")
            return None, tuple(flags)
        return abs(position.quantity) / safe, ()
    if config.default_liquidity_horizon_days is not None:
        flags.append("default_liquidity_horizon_used")
        return config.default_liquidity_horizon_days, tuple(flags)
    flags.append("liquidity_horizon_unknown")
    return None, tuple(flags)


def _factor_exposures(
    registry: InstrumentRegistry,
    instrument_id: str,
    exposure_base: float,
) -> Mapping[str, float]:
    return {
        factor_id: loading * exposure_base
        for factor_id, loading in registry.loadings_for(instrument_id).items()
    }


def _base_flags(position: PositionRecord) -> list[str]:
    flags: list[str] = []
    if position.quality == DataQuality.STALE:
        flags.append("stale_valuation")
    elif position.quality == DataQuality.DEGRADED:
        flags.append("degraded_valuation")
    elif position.quality in {DataQuality.MISSING, DataQuality.INVALID}:
        flags.append("invalid_valuation")
    return flags


def _linear_exposure(
    position: PositionRecord,
    spec: InstrumentSpec,
    registry: InstrumentRegistry,
    market_value: float,
    base_currency: str,
    config: NormalizationConfig,
) -> NormalizedExposure:
    flags = _base_flags(position)
    gross = abs(market_value)
    max_loss: float | None
    if position.quantity > 0:
        max_loss = gross
    else:
        max_loss = None
        flags.append("short_position_max_loss_unbounded")
    configured_max_loss = _metadata_float(position.metadata, "max_loss")
    if configured_max_loss is not None:
        if configured_max_loss < 0:
            raise InvalidExposureError("max_loss metadata must be non-negative")
        max_loss = configured_max_loss
    margin = _metadata_float(position.metadata, "margin_requirement")
    liquidity, liquidity_flags = _liquidity_horizon(position, config)
    flags.extend(liquidity_flags)
    return NormalizedExposure(
        instrument_id=position.instrument_id,
        section=position.section,
        asset_class=spec.asset_class,
        venue=position.venue,
        currency=position.currency,
        base_currency=base_currency,
        market_value=market_value,
        gross_market_value=gross,
        method="linear_market_value",
        model_version="p11-linear-1",
        quality=position.quality,
        as_of=position.as_of,
        delta_equivalent=market_value,
        max_loss=max_loss,
        margin_requirement=margin,
        liquidity_horizon_days=liquidity,
        factor_exposures=_factor_exposures(registry, position.instrument_id, market_value),
        currency_exposures={position.currency: market_value},
        uncertainty_flags=tuple(dict.fromkeys(flags)),
    )


def _option_exposure(
    position: PositionRecord,
    spec: InstrumentSpec,
    registry: InstrumentRegistry,
    market_value: float,
    fx_rates: Sequence[FxRate],
    base_currency: str,
    now: datetime,
    freshness: FreshnessPolicy,
    config: NormalizationConfig,
) -> NormalizedExposure:
    option = position.option
    if option is None:
        raise InvalidExposureError(
            f"option position {position.position_id} is missing contract-level Greeks"
        )
    flags = _base_flags(position)
    signed_units = position.quantity * position.multiplier
    underlying_currency = spec.metadata.get("underlying_currency", position.currency)
    fx_underlying, fx_quality = _convert(
        1.0, underlying_currency, base_currency, fx_rates, now, freshness
    )
    fx_premium, premium_quality = _convert(
        1.0, position.currency, base_currency, fx_rates, now, freshness
    )
    if fx_quality != DataQuality.VALID:
        flags.append("stale_underlying_fx")
    if premium_quality != DataQuality.VALID:
        flags.append("stale_premium_fx")

    delta_units = signed_units * option.delta
    delta_base = delta_units * fx_underlying
    delta_equivalent = delta_base * option.underlying_price
    gamma_units = signed_units * option.gamma * fx_underlying
    vega_units = signed_units * option.vega * fx_premium
    theta_units = signed_units * option.theta * fx_premium

    max_loss: float | None = None
    if option.max_loss_per_contract is not None:
        max_loss = (
            option.max_loss_per_contract * abs(position.quantity) * position.multiplier * fx_premium
        )
    else:
        flags.append("option_max_loss_undefined")
    margin: float | None = None
    if option.margin_per_contract is not None:
        margin = (
            option.margin_per_contract
            * abs(position.quantity)
            * position.multiplier
            * fx_premium
        )
    else:
        flags.append("option_margin_unknown")
    if option.time_to_expiry_years <= 0:
        flags.append("option_expired")
    if abs(option.gamma) > 0 or abs(option.vega) > 0:
        flags.append("nonlinear_option_risk")
    liquidity, liquidity_flags = _liquidity_horizon(position, config)
    flags.extend(liquidity_flags)
    return NormalizedExposure(
        instrument_id=position.instrument_id,
        section=position.section,
        asset_class=spec.asset_class,
        venue=position.venue,
        currency=position.currency,
        base_currency=base_currency,
        market_value=market_value,
        gross_market_value=abs(market_value),
        method="option_greeks_local",
        model_version=option.model_version,
        quality=worst_quality(position.quality, fx_quality, premium_quality),
        as_of=position.as_of,
        delta_equivalent=delta_equivalent,
        delta=delta_base,
        gamma=gamma_units,
        vega=vega_units,
        theta=theta_units,
        underlying_price=option.underlying_price,
        underlying_currency=underlying_currency,
        max_loss=max_loss,
        margin_requirement=margin,
        liquidity_horizon_days=liquidity,
        factor_exposures=_factor_exposures(registry, position.instrument_id, delta_equivalent),
        currency_exposures={underlying_currency: delta_equivalent},
        uncertainty_flags=tuple(dict.fromkeys(flags)),
    )


def _prediction_exposure(
    position: PositionRecord,
    spec: InstrumentSpec,
    registry: InstrumentRegistry,
    market_value: float,
    fx_rate: float,
    base_currency: str,
    config: NormalizationConfig,
) -> NormalizedExposure:
    prediction = position.prediction
    if prediction is None:
        raise InvalidExposureError(
            f"prediction position {position.position_id} is missing its payoff schedule"
        )
    flags = _base_flags(position)
    signed_units = position.quantity * position.multiplier
    cost = signed_units * prediction.entry_price_per_contract
    fees = abs(signed_units) * prediction.fee_per_contract
    pnls = [
        signed_units * outcome.payout_per_contract - cost - fees
        for outcome in prediction.outcomes
    ]
    max_loss = max(0.0, max(-value for value in pnls))
    expected_payoff: float | None = None
    expected_pnl: float | None = None
    if all(outcome.probability is not None for outcome in prediction.outcomes):
        expected_payoff = sum(
            (outcome.probability or 0.0) * signed_units * outcome.payout_per_contract
            for outcome in prediction.outcomes
        )
        expected_pnl = expected_payoff - cost - fees
    else:
        flags.append("prediction_probabilities_missing")
    if prediction.settlement_status.lower() != "open":
        flags.append("prediction_not_open")
    if position.quantity < 0:
        flags.append("prediction_short_position")
    liquidity, liquidity_flags = _liquidity_horizon(position, config)
    flags.extend(liquidity_flags)
    max_loss_base = max_loss * fx_rate
    expected_pnl_base = expected_pnl * fx_rate if expected_pnl is not None else None
    return NormalizedExposure(
        instrument_id=position.instrument_id,
        section=position.section,
        asset_class=spec.asset_class,
        venue=position.venue,
        currency=position.currency,
        base_currency=base_currency,
        market_value=market_value,
        gross_market_value=abs(market_value),
        method="prediction_payoff_schedule",
        model_version="p11-prediction-1",
        quality=position.quality,
        as_of=position.as_of,
        delta_equivalent=expected_pnl_base,
        max_loss=max_loss_base,
        expected_payoff=expected_payoff * fx_rate if expected_payoff is not None else None,
        liquidity_horizon_days=liquidity,
        factor_exposures=_factor_exposures(registry, position.instrument_id, max_loss_base),
        currency_exposures={position.currency: market_value},
        uncertainty_flags=tuple(dict.fromkeys(flags)),
    )


def _forex_exposure(
    position: PositionRecord,
    spec: InstrumentSpec,
    registry: InstrumentRegistry,
    market_value: float,
    fx_rates: Sequence[FxRate],
    base_currency: str,
    now: datetime,
    freshness: FreshnessPolicy,
    config: NormalizationConfig,
) -> NormalizedExposure:
    flags = _base_flags(position)
    if not spec.base_currency or not spec.quote_currency:
        raise InvalidExposureError(f"FX instrument {spec.instrument_id} is missing currency legs")
    notional_base_ccy = position.quantity * position.multiplier
    base_leg, base_quality = _convert(
        notional_base_ccy, spec.base_currency, base_currency, fx_rates, now, freshness
    )
    spot = _metadata_float(position.metadata, "spot_rate")
    if spot is None:
        spot_result = convert_amount(
            1.0,
            spec.base_currency,
            spec.quote_currency,
            fx_rates,
            now,
            max_age_seconds=freshness.max_fx_age_seconds,
        )
        if not spot_result.ok or spot_result.rate is None:
            raise MissingFxRateError(
                spot_result.reason
                or f"missing {spec.base_currency}/{spec.quote_currency} spot rate"
            )
        spot = spot_result.rate
        spot_quality = spot_result.quality
    else:
        if spot <= 0:
            raise InvalidExposureError("spot_rate metadata must be positive")
        spot_quality = DataQuality.VALID
    quote_leg, quote_quality = _convert(
        -notional_base_ccy * spot,
        spec.quote_currency,
        base_currency,
        fx_rates,
        now,
        freshness,
    )
    if base_quality != DataQuality.VALID or quote_quality != DataQuality.VALID:
        flags.append("stale_fx_leg")
    if spot_quality != DataQuality.VALID:
        flags.append("stale_fx_spot")
    max_loss: float | None = None
    configured_max_loss = _metadata_float(position.metadata, "max_loss")
    if configured_max_loss is not None:
        if configured_max_loss < 0:
            raise InvalidExposureError("max_loss metadata must be non-negative")
        max_loss = configured_max_loss
    else:
        flags.append("fx_max_loss_undefined")
    margin: float | None = None
    configured_margin = _metadata_float(position.metadata, "margin_requirement")
    if configured_margin is not None:
        margin = configured_margin
    else:
        flags.append("fx_margin_unknown")
    liquidity, liquidity_flags = _liquidity_horizon(position, config)
    flags.extend(liquidity_flags)
    return NormalizedExposure(
        instrument_id=position.instrument_id,
        section=position.section,
        asset_class=spec.asset_class,
        venue=position.venue,
        currency=position.currency,
        base_currency=base_currency,
        market_value=market_value,
        gross_market_value=abs(market_value),
        method="fx_currency_legs",
        model_version="p11-fx-1",
        quality=worst_quality(position.quality, base_quality, quote_quality),
        as_of=position.as_of,
        delta_equivalent=base_leg,
        max_loss=max_loss,
        margin_requirement=margin,
        liquidity_horizon_days=liquidity,
        factor_exposures=_factor_exposures(registry, position.instrument_id, base_leg),
        currency_exposures={
            spec.base_currency: base_leg,
            spec.quote_currency: quote_leg,
        },
        uncertainty_flags=tuple(dict.fromkeys(flags)),
    )


def normalize_position(
    position: PositionRecord,
    registry: InstrumentRegistry,
    fx_rates: Sequence[FxRate],
    base_currency: str,
    now: datetime,
    *,
    freshness: FreshnessPolicy,
    config: NormalizationConfig = DEFAULT_NORMALIZATION_CONFIG,
) -> NormalizedExposure:
    """Normalize one position, raising on unsupported or incomplete inputs."""
    current = require_aware(now, "now")
    try:
        spec = registry.require(position.instrument_id)
    except UnknownInstrumentError as exc:
        raise UnsupportedInstrumentError(str(exc)) from exc
    if spec.section != position.section:
        raise InvalidExposureError(
            f"instrument {spec.instrument_id} section {spec.section.value} does not match "
            f"position section {position.section.value}"
        )
    if spec.asset_class == AssetClass.CASH:
        raise UnsupportedInstrumentError("cash positions are represented as capital, not exposure")
    if spec.instrument_type in {InstrumentType.CALL, InstrumentType.PUT}:
        if spec.asset_class != AssetClass.OPTION:
            raise InvalidExposureError("option instrument type must use the option asset class")
        market_value, _ = _convert(
            position.market_value,
            position.currency,
            base_currency,
            fx_rates,
            current,
            freshness,
        )
        return _option_exposure(
            position,
            spec,
            registry,
            market_value,
            fx_rates,
            base_currency,
            current,
            freshness,
            config,
        )
    if spec.asset_class == AssetClass.PREDICTION:
        conversion = convert_amount(
            position.market_value,
            position.currency,
            base_currency,
            fx_rates,
            current,
            max_age_seconds=freshness.max_fx_age_seconds,
        )
        if not conversion.ok or conversion.amount is None or conversion.rate is None:
            raise MissingFxRateError(
                conversion.reason
                or f"missing FX conversion for {position.currency} to {base_currency}"
            )
        exposure = _prediction_exposure(
            position,
            spec,
            registry,
            conversion.amount,
            conversion.rate,
            base_currency,
            config,
        )
        if conversion.quality != DataQuality.VALID:
            return replace(
                exposure,
                quality=worst_quality(exposure.quality, conversion.quality),
                uncertainty_flags=tuple(
                    dict.fromkeys(exposure.uncertainty_flags + ("stale_fx",))
                ),
            )
        return exposure
    if spec.instrument_type == InstrumentType.FX_PAIR:
        market_value, _ = _convert(
            position.market_value,
            position.currency,
            base_currency,
            fx_rates,
            current,
            freshness,
        )
        return _forex_exposure(
            position,
            spec,
            registry,
            market_value,
            fx_rates,
            base_currency,
            current,
            freshness,
            config,
        )
    if spec.asset_class in {AssetClass.EQUITY, AssetClass.CRYPTO}:
        market_value, fx_quality = _convert(
            position.market_value,
            position.currency,
            base_currency,
            fx_rates,
            current,
            freshness,
        )
        exposure = _linear_exposure(
            position, spec, registry, market_value, base_currency, config
        )
        if fx_quality != DataQuality.VALID:
            return replace(
                exposure,
                quality=worst_quality(exposure.quality, fx_quality),
                uncertainty_flags=tuple(
                    dict.fromkeys(exposure.uncertainty_flags + ("stale_fx",))
                ),
            )
        return exposure
    raise UnsupportedInstrumentError(
        f"no exposure model for {spec.asset_class.value}/{spec.instrument_type.value}"
    )


def normalize_open_order(
    order: OpenOrderRecord,
    registry: InstrumentRegistry,
    fx_rates: Sequence[FxRate],
    base_currency: str,
    now: datetime,
    *,
    freshness: FreshnessPolicy,
    config: NormalizationConfig = DEFAULT_NORMALIZATION_CONFIG,
) -> NormalizedExposure:
    """Represent the possible incremental exposure of an unfilled order."""
    current = require_aware(now, "now")
    try:
        spec = registry.require(order.instrument_id)
    except UnknownInstrumentError as exc:
        raise UnsupportedInstrumentError(str(exc)) from exc
    signed_notional, fx_quality = _convert(
        order.signed_incremental_notional,
        order.currency,
        base_currency,
        fx_rates,
        current,
        freshness,
    )
    flags = ["open_order_exposure"]
    if fx_quality != DataQuality.VALID:
        flags.append("stale_fx")
    if order.reduces_position:
        flags.append("order_reduces_position")
    if order.state.lower() in {"unknown", "pending", "cancel_pending"}:
        flags.append("unknown_order_state")
    if spec.instrument_type in {InstrumentType.CALL, InstrumentType.PUT}:
        flags.append("option_order_exposure_approximate")
        max_loss = abs(signed_notional) if order.side.value == "buy" else None
    elif spec.asset_class == AssetClass.PREDICTION:
        flags.append("prediction_order_exposure_approximate")
        max_loss = abs(signed_notional)
    else:
        max_loss = abs(signed_notional) if order.side.value == "buy" else None
    if max_loss is None:
        flags.append("order_max_loss_unbounded")
    return NormalizedExposure(
        instrument_id=order.instrument_id,
        section=order.section,
        asset_class=spec.asset_class,
        venue=order.venue,
        currency=order.currency,
        base_currency=base_currency,
        market_value=signed_notional,
        gross_market_value=abs(signed_notional),
        method="open_order_incremental",
        model_version="p11-order-1",
        quality=worst_quality(order.quality, fx_quality),
        as_of=order.as_of,
        delta_equivalent=signed_notional,
        max_loss=max_loss,
        factor_exposures=_factor_exposures(registry, order.instrument_id, signed_notional),
        currency_exposures={order.currency: signed_notional},
        uncertainty_flags=tuple(dict.fromkeys(flags)),
    )


def _hhi(values: Sequence[float]) -> float:
    total = sum(values)
    if total <= 0:
        return 0.0
    return sum((value / total) ** 2 for value in values)


def _concentration_metrics(
    exposures: Sequence[NormalizedExposure],
    registry: InstrumentRegistry,
) -> Mapping[str, float]:
    instrument_values = [exposure.gross_market_value for exposure in exposures]
    section_values: dict[str, float] = {}
    venue_values: dict[str, float] = {}
    issuer_values: dict[str, float] = {}
    for exposure in exposures:
        section_values[exposure.section.value] = (
            section_values.get(exposure.section.value, 0.0) + exposure.gross_market_value
        )
        venue_values[exposure.venue] = (
            venue_values.get(exposure.venue, 0.0) + exposure.gross_market_value
        )
        spec = registry.get(exposure.instrument_id)
        issuer = spec.issuer_id if spec and spec.issuer_id else "unknown"
        issuer_values[issuer] = issuer_values.get(issuer, 0.0) + exposure.gross_market_value
    total = sum(instrument_values)
    top_instrument = max(instrument_values, default=0.0)
    top_section = max(section_values.values(), default=0.0)
    return {
        "hhi_instrument": _hhi(instrument_values),
        "hhi_section": _hhi(tuple(section_values.values())),
        "hhi_venue": _hhi(tuple(venue_values.values())),
        "hhi_issuer": _hhi(tuple(issuer_values.values())),
        "top_instrument_share": top_instrument / total if total > 0 else 0.0,
        "top_section_share": top_section / total if total > 0 else 0.0,
        "gross_exposure": total,
    }


def _liquidity_metrics(
    exposures: Sequence[NormalizedExposure],
    config: NormalizationConfig,
) -> Mapping[str, float]:
    total = sum(exposure.gross_market_value for exposure in exposures)
    known = [
        exposure
        for exposure in exposures
        if exposure.liquidity_horizon_days is not None
    ]
    known_gross = sum(exposure.gross_market_value for exposure in known)
    weighted = (
        sum(
            (exposure.liquidity_horizon_days or 0.0) * exposure.gross_market_value
            for exposure in known
        )
        / known_gross
        if known_gross > 0
        else 0.0
    )
    illiquid = sum(
        exposure.gross_market_value
        for exposure in known
        if (exposure.liquidity_horizon_days or 0.0) > config.illiquid_threshold_days
    )
    return {
        "weighted_average_horizon_days": weighted,
        "max_horizon_days": max(
            (exposure.liquidity_horizon_days or 0.0 for exposure in known), default=0.0
        ),
        "illiquid_share": illiquid / total if total > 0 else 0.0,
        "unknown_horizon_share": (total - known_gross) / total if total > 0 else 0.0,
        "total_gross_exposure": total,
    }


def _aggregate_section(
    section: Section,
    exposures: Sequence[NormalizedExposure],
    failures: Sequence[NormalizationFailure],
) -> SectionExposure:
    section_exposures = [exposure for exposure in exposures if exposure.section == section]
    section_failures = [failure for failure in failures if failure.section == section]
    if not section_exposures:
        if section_failures:
            return SectionExposure(
                section=section,
                gross_market_value=0.0,
                net_market_value=0.0,
                delta_equivalent=None,
                max_loss=None,
                margin_requirement=None,
                expected_payoff=None,
                liquidity_horizon_days=None,
                instrument_count=0,
                uncertainty_flags=("exposure_failure",),
            )
        return SectionExposure(
            section=section,
            gross_market_value=0.0,
            net_market_value=0.0,
            delta_equivalent=0.0,
            max_loss=0.0,
            margin_requirement=0.0,
            expected_payoff=0.0,
            liquidity_horizon_days=0.0,
            instrument_count=0,
        )
    gross = sum(exposure.gross_market_value for exposure in section_exposures)
    net = sum(exposure.market_value for exposure in section_exposures)
    delta_values = [exposure.delta_equivalent for exposure in section_exposures]
    max_loss_values = [exposure.max_loss for exposure in section_exposures]
    margin_values = [exposure.margin_requirement for exposure in section_exposures]
    payoff_values = [exposure.expected_payoff for exposure in section_exposures]
    flags = tuple(
        dict.fromkeys(
            flag
            for exposure in section_exposures
            for flag in exposure.uncertainty_flags
        )
    )
    known_gross = sum(
        exposure.gross_market_value
        for exposure in section_exposures
        if exposure.liquidity_horizon_days is not None
    )
    weighted_horizon = (
        sum(
            (exposure.liquidity_horizon_days or 0.0) * exposure.gross_market_value
            for exposure in section_exposures
            if exposure.liquidity_horizon_days is not None
        )
        / known_gross
        if known_gross > 0
        else None
    )
    return SectionExposure(
        section=section,
        gross_market_value=gross,
        net_market_value=net,
        delta_equivalent=(
            sum(value for value in delta_values if value is not None)
            if all(value is not None for value in delta_values)
            else None
        ),
        max_loss=(
            sum(value for value in max_loss_values if value is not None)
            if all(value is not None for value in max_loss_values)
            else None
        ),
        margin_requirement=(
            sum(value for value in margin_values if value is not None)
            if all(value is not None for value in margin_values)
            else None
        ),
        expected_payoff=(
            sum(value for value in payoff_values if value is not None)
            if all(value is not None for value in payoff_values)
            else None
        ),
        liquidity_horizon_days=weighted_horizon,
        instrument_count=len(section_exposures),
        uncertainty_flags=flags,
    )


def build_exposure_snapshot(
    snapshot: PortfolioSnapshot,
    registry: InstrumentRegistry,
    *,
    now: datetime | None = None,
    freshness: FreshnessPolicy,
    config: NormalizationConfig = DEFAULT_NORMALIZATION_CONFIG,
) -> ExposureSnapshot:
    """Normalize all positions and open orders, recording every failure."""
    current = require_aware(now or utc_now(), "now")
    exposures: list[NormalizedExposure] = []
    failures: list[NormalizationFailure] = []

    for position in snapshot.positions:
        try:
            exposures.append(
                normalize_position(
                    position,
                    registry,
                    snapshot.fx_rates,
                    snapshot.base_currency,
                    current,
                    freshness=freshness,
                    config=config,
                )
            )
        except NormalizationError as exc:
            failures.append(
                NormalizationFailure(
                    instrument_id=position.instrument_id,
                    section=position.section,
                    reason=type(exc).__name__,
                    detail=str(exc),
                )
            )

    order_exposures: list[NormalizedExposure] = []
    if config.include_open_orders:
        for order in snapshot.open_orders:
            try:
                order_exposures.append(
                    normalize_open_order(
                        order,
                        registry,
                        snapshot.fx_rates,
                        snapshot.base_currency,
                        current,
                        freshness=freshness,
                        config=config,
                    )
                )
            except NormalizationError as exc:
                failures.append(
                    NormalizationFailure(
                        instrument_id=order.instrument_id,
                        section=order.section,
                        reason=type(exc).__name__,
                        detail=str(exc),
                    )
                )

    all_exposures = exposures + order_exposures
    section_exposures = {
        section.value: _aggregate_section(section, all_exposures, failures)
        for section in Section
    }
    factor_totals: dict[str, float] = {}
    currency_totals: dict[str, float] = {}
    for exposure in all_exposures:
        for factor_id, value in exposure.factor_exposures.items():
            factor_totals[factor_id] = factor_totals.get(factor_id, 0.0) + value
        for currency, value in exposure.currency_exposures.items():
            currency_totals[currency] = currency_totals.get(currency, 0.0) + value

    # Coverage denominator uses source-reported values converted to base.
    denominator = 0.0
    denominator_complete = True
    for position in snapshot.positions:
        conversion = convert_amount(
            position.market_value,
            position.currency,
            snapshot.base_currency,
            snapshot.fx_rates,
            current,
            max_age_seconds=freshness.max_fx_age_seconds,
        )
        if not conversion.ok or conversion.amount is None:
            denominator_complete = False
            continue
        denominator += abs(conversion.amount)
    numerator = sum(exposure.gross_market_value for exposure in exposures)
    coverage_ratio = (
        1.0 if denominator == 0 and not failures else (numerator / denominator if denominator else 0.0)
    )
    if not denominator_complete:
        coverage_ratio = 0.0

    uncertainty_flags = tuple(
        dict.fromkeys(
            flag for exposure in all_exposures for flag in exposure.uncertainty_flags
        )
    )
    failure_flags = tuple(
        f"{failure.reason}:{failure.instrument_id}" for failure in failures
    )
    snapshot_complete = (
        snapshot.completeness_status == SnapshotCompleteness.COMPLETE
        and snapshot.data_quality_status == DataQuality.VALID
    )
    is_complete = (
        not failures
        and denominator_complete
        and coverage_ratio >= 0.999
        and snapshot_complete
    )
    exposure_id = fingerprint(
        "expo",
        snapshot.snapshot_id,
        config.normalization_version,
        registry.version,
        tuple(exposures),
        tuple(order_exposures),
        tuple(failures),
    )
    return ExposureSnapshot(
        exposure_snapshot_id=exposure_id,
        portfolio_snapshot_id=snapshot.snapshot_id,
        normalization_version=config.normalization_version,
        base_currency=snapshot.base_currency,
        instrument_exposures=tuple(exposures),
        order_exposures=tuple(order_exposures),
        section_exposures=section_exposures,
        factor_exposures=factor_totals,
        currency_exposures=currency_totals,
        concentration_metrics=_concentration_metrics(all_exposures, registry),
        liquidity_metrics=_liquidity_metrics(all_exposures, config),
        uncertainty_flags=tuple(dict.fromkeys(uncertainty_flags + failure_flags)),
        failures=tuple(failures),
        is_complete=is_complete,
        coverage_ratio=coverage_ratio,
        created_at=current,
    )


__all__ = [
    "DEFAULT_NORMALIZATION_CONFIG",
    "DEFAULT_NORMALIZATION_VERSION",
    "InvalidExposureError",
    "MissingFxRateError",
    "NormalizationConfig",
    "NormalizationError",
    "UnsupportedInstrumentError",
    "build_exposure_snapshot",
    "normalize_open_order",
    "normalize_position",
]
