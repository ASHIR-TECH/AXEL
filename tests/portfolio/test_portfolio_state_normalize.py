"""Snapshot construction, FX conversion, and exposure normalization tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from support import (
    NOW,
    all_live_statuses,
    make_exposure,
    make_fx_rate,
    make_option_position,
    make_prediction_position,
    make_prediction_registry,
    make_registry,
    make_snapshot,
    stock_position,
)

from axel.core.types import Section
from axel.portfolio.normalize import (
    MissingFxRateError,
    UnsupportedInstrumentError,
    build_exposure_snapshot,
    normalize_position,
)
from axel.portfolio.state import (
    DEFAULT_FRESHNESS,
    SnapshotInvariantError,
    build_portfolio_snapshot,
    convert_amount,
)
from axel.portfolio.types import (
    AssetClass,
    DataQuality,
    InstrumentSpec,
    InstrumentType,
    SectionReadiness,
    SectionStatus,
)


def test_direct_and_inverse_fx_conversion() -> None:
    rate = make_fx_rate()
    direct = convert_amount(100.0, "EUR", "USD", (rate,), NOW)
    assert direct.ok
    assert direct.amount == pytest.approx(110.0)
    assert direct.rate == pytest.approx(1.10)
    inverse = convert_amount(110.0, "USD", "EUR", (rate,), NOW)
    assert inverse.ok
    assert inverse.amount == pytest.approx(100.0)


def test_missing_or_stale_fx_fails_closed() -> None:
    stale = make_fx_rate(now=NOW - timedelta(seconds=3_600))
    result = convert_amount(100.0, "EUR", "USD", (stale,), NOW, max_age_seconds=300)
    assert result.ok
    assert result.amount == pytest.approx(110.0)
    assert result.quality in {DataQuality.STALE, DataQuality.DEGRADED}
    missing = convert_amount(100.0, "GBP", "USD", (make_fx_rate(),), NOW)
    assert not missing.ok
    assert missing.amount is None


def test_snapshot_identity_and_capital_summary() -> None:
    snapshot = make_snapshot()
    assert snapshot.is_usable_for_risk_increase
    assert snapshot.capital_summary.equity_value == pytest.approx(100_000.0)
    assert snapshot.capital_summary.available_to_trade == pytest.approx(100_000.0)
    assert snapshot.section(Section.STOCKS).readiness == SectionReadiness.LIVE_ELIGIBLE
    assert snapshot.position("pos_stock_a") is not None


def test_duplicate_position_identity_is_rejected() -> None:
    position = stock_position("stock_a")
    with pytest.raises(SnapshotInvariantError, match="duplicate"):
        build_portfolio_snapshot(
            accounts=(),
            positions=(position, position),
            open_orders=(),
            commitments=(),
            section_status=all_live_statuses(),
            now=NOW,
        )


def test_stale_section_status_blocks_risk_increase() -> None:
    stale_status = tuple(
        SectionStatus(
            section=section,
            readiness=SectionReadiness.LIVE_ELIGIBLE,
            data_quality=DataQuality.VALID,
            reconciliation_status=snapshot_status.reconciliation_status,
            last_update=NOW - timedelta(seconds=3_600),
            source="test_status",
        )
        for section, snapshot_status in (
            (status.section, status) for status in all_live_statuses()
        )
    )
    snapshot = make_snapshot(statuses=stale_status)
    assert not snapshot.is_usable_for_risk_increase
    assert snapshot.data_quality_status in {DataQuality.STALE, DataQuality.DEGRADED}


def test_missing_fx_makes_capital_unknown() -> None:
    snapshot = make_snapshot(
        positions=(stock_position("stock_a", quantity=100.0, price=100.0),),
    )
    # A snapshot with a foreign-currency account but no FX rate must not
    # silently treat the foreign amount as base currency.
    from axel.portfolio.types import AccountCapital

    account = AccountCapital(
        account_id="eur_acct",
        venue="test",
        currency="EUR",
        cash_balance=10_000.0,
        equity_value=10_000.0,
        account_equity=10_000.0,
        reserved_order_capacity=0.0,
        margin_requirement=0.0,
        as_of=NOW,
        source="test",
        available_to_trade=10_000.0,
    )
    built = build_portfolio_snapshot(
        accounts=(account,),
        positions=(),
        open_orders=(),
        commitments=(),
        section_status=all_live_statuses(),
        now=NOW,
        base_currency="USD",
        snapshot_id="snap_missing_fx",
    )
    assert built.capital_summary.equity_value is None
    assert not built.capital_summary.conversion_complete
    assert not built.is_usable_for_risk_increase
    assert snapshot.capital_summary.equity_value is not None


def test_linear_exposure_and_complete_coverage() -> None:
    snapshot = make_snapshot()
    exposure = make_exposure(snapshot)
    assert exposure.is_complete
    assert exposure.coverage_ratio == pytest.approx(1.0)
    assert len(exposure.instrument_exposures) == 2
    assert exposure.section(Section.STOCKS).gross_market_value == pytest.approx(20_000.0)
    assert exposure.section(Section.STOCKS).net_market_value == pytest.approx(20_000.0)
    assert exposure.concentration_metrics["hhi_instrument"] == pytest.approx(0.5)


def test_unsupported_instrument_is_recorded_not_ignored() -> None:
    snapshot = make_snapshot(
        positions=(
            stock_position("stock_a"),
            stock_position("unknown_stock"),
        )
    )
    exposure = build_exposure_snapshot(
        snapshot,
        make_registry(),
        now=NOW,
        freshness=DEFAULT_FRESHNESS,
    )
    assert not exposure.is_complete
    assert exposure.failures
    assert exposure.failures[0].reason == "UnsupportedInstrumentError"
    assert any("UnsupportedInstrumentError" in flag for flag in exposure.uncertainty_flags)


def test_option_exposure_uses_delta_equivalent_and_greeks() -> None:
    snapshot = make_snapshot(positions=(make_option_position(),))
    registry = make_registry(include_option=True)
    exposure = build_exposure_snapshot(
        snapshot,
        registry,
        now=NOW,
        freshness=DEFAULT_FRESHNESS,
    )
    assert exposure.is_complete
    option = exposure.instrument_exposures[0]
    assert option.asset_class == AssetClass.OPTION
    assert option.delta_equivalent == pytest.approx(5_500.0)
    assert option.delta == pytest.approx(55.0)
    assert option.gamma == pytest.approx(2.0)
    assert option.vega == pytest.approx(2_000.0)
    assert option.theta == pytest.approx(-500.0)
    assert option.market_value == pytest.approx(500.0)


def test_prediction_exposure_uses_payoff_max_loss() -> None:
    snapshot = make_snapshot(positions=(make_prediction_position(),))
    exposure = build_exposure_snapshot(
        snapshot,
        make_prediction_registry(),
        now=NOW,
        freshness=DEFAULT_FRESHNESS,
    )
    assert exposure.is_complete
    prediction = exposure.instrument_exposures[0]
    assert prediction.asset_class == AssetClass.PREDICTION
    assert prediction.max_loss == pytest.approx(4.0)
    assert prediction.expected_payoff is None
    assert "prediction_probabilities_missing" in prediction.uncertainty_flags


def test_fx_exposure_keeps_both_currency_legs() -> None:
    registry = make_registry()
    registry.register(
        InstrumentSpec(
            instrument_id="eurusd",
            symbol="EURUSD",
            section=Section.FOREX,
            asset_class=AssetClass.FOREX,
            instrument_type=InstrumentType.FX_PAIR,
            venue="test",
            currency="USD",
            settlement_currency="USD",
            base_currency="EUR",
            quote_currency="USD",
        )
    )
    position = stock_position(
        "eurusd",
        quantity=1_000.0,
        price=1.10,
        section=Section.FOREX,
    )
    snapshot = make_snapshot(
        positions=(position,),
        fx_rates=(make_fx_rate(),),
    )
    exposure = build_exposure_snapshot(
        snapshot,
        registry,
        now=NOW,
        freshness=DEFAULT_FRESHNESS,
    )
    assert exposure.is_complete
    fx = exposure.instrument_exposures[0]
    assert fx.delta_equivalent == pytest.approx(1_100.0)
    assert fx.currency_exposures["EUR"] == pytest.approx(1_100.0)
    assert fx.currency_exposures["USD"] == pytest.approx(-1_100.0)
    assert "fx_max_loss_undefined" in fx.uncertainty_flags


def test_normalize_position_requires_registered_instrument() -> None:
    snapshot = make_snapshot(positions=(stock_position("unknown_stock"),))
    with pytest.raises(UnsupportedInstrumentError):
        normalize_position(
            snapshot.positions[0],
            make_registry(),
            (),
            "USD",
            NOW,
            freshness=DEFAULT_FRESHNESS,
        )


def test_normalize_position_missing_fx_raises() -> None:
    registry = make_registry()
    registry.register(
        InstrumentSpec(
            instrument_id="eurusd",
            symbol="EURUSD",
            section=Section.FOREX,
            asset_class=AssetClass.FOREX,
            instrument_type=InstrumentType.FX_PAIR,
            venue="test",
            currency="USD",
            settlement_currency="USD",
            base_currency="EUR",
            quote_currency="USD",
        )
    )
    position = stock_position(
        "eurusd",
        quantity=1_000.0,
        price=1.10,
        section=Section.FOREX,
    )
    with pytest.raises(MissingFxRateError):
        normalize_position(
            position,
            registry,
            (),
            "USD",
            NOW,
            freshness=DEFAULT_FRESHNESS,
        )
