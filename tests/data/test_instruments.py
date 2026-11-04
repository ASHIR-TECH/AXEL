from datetime import UTC, datetime, timedelta

import pytest

from axel.data.instruments import (
    AssetClass,
    ContractType,
    Instrument,
    InstrumentConflictError,
    InstrumentRegistry,
    instrument_symbol,
    instrument_venue,
    make_instrument_id,
)


def _equity(**overrides) -> Instrument:
    values = {
        "instrument_id": "equity:alpaca:AAPL",
        "asset_class": AssetClass.EQUITY,
        "provider_id": "alpaca",
        "provider_symbol": "AAPL",
        "venue": "alpaca-iex",
        "quote_currency": "USD",
        "settlement_currency": "USD",
        "exchange": "NASDAQ",
        "tick_size": 0.01,
        "lot_size": 1.0,
        "minimum_quantity": 1.0,
        "trading_calendar": "US_EQUITY",
        "timezone": "America/New_York",
    }
    values.update(overrides)
    return Instrument(**values)


def _option(**overrides) -> Instrument:
    values = {
        "instrument_id": "option:deribit:BTC-20261231-100000-C",
        "asset_class": AssetClass.OPTION,
        "provider_id": "deribit",
        "provider_symbol": "BTC-20261231-100000-C",
        "venue": "deribit",
        "contract_type": ContractType.OPTION,
        "base_currency": "BTC",
        "quote_currency": "USD",
        "settlement_currency": "BTC",
        "tick_size": 0.0005,
        "lot_size": 1.0,
        "minimum_quantity": 1.0,
        "expiry": datetime(2026, 12, 31, 8, tzinfo=UTC),
        "strike": 100_000.0,
        "option_type": "call",
        "multiplier": 1.0,
        "timezone": "UTC",
    }
    values.update(overrides)
    return Instrument(**values)


def test_equity_round_trip_preserves_identity_and_fingerprint() -> None:
    instrument = _equity()
    restored = Instrument.from_dict(instrument.to_dict())
    assert restored == instrument
    assert restored.fingerprint() == instrument.fingerprint()
    assert restored.provider_key == ("alpaca", "AAPL")
    assert not restored.is_derivative


def test_option_requires_contract_terms_and_rejects_wrong_asset_class() -> None:
    option = _option()
    assert option.is_derivative
    assert option.option_type == "call"

    with pytest.raises(ValueError, match="expiry"):
        _option(expiry=None)
    with pytest.raises(ValueError, match="strike"):
        _option(strike=None)
    with pytest.raises(ValueError, match="option_type"):
        _option(option_type="straddle")
    with pytest.raises(ValueError, match="only valid for option"):
        _equity(strike=100.0)
    with pytest.raises(ValueError, match="contract_type='option'"):
        _equity(contract_type=ContractType.OPTION)


def test_crypto_and_fx_require_distinct_base_and_quote_currencies() -> None:
    crypto = _equity(
        instrument_id="crypto:binance:BTCUSDT",
        asset_class=AssetClass.CRYPTO,
        provider_id="binance",
        provider_symbol="BTCUSDT",
        venue="binance",
        base_currency="BTC",
        quote_currency="USDT",
        exchange="BINANCE",
    )
    assert crypto.base_currency == "BTC"

    with pytest.raises(ValueError, match="base_currency"):
        _equity(
            instrument_id="crypto:binance:BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            base_currency=None,
        )
    with pytest.raises(ValueError, match="must differ"):
        _equity(
            instrument_id="forex:oanda:EURUSD",
            asset_class=AssetClass.FOREX,
            base_currency="USD",
            quote_currency="USD",
        )


def test_execution_metadata_fails_closed_when_missing() -> None:
    incomplete = _equity(tick_size=None)
    with pytest.raises(ValueError, match="tick_size"):
        incomplete.require_execution_metadata()
    with pytest.raises(ValueError, match="expired"):
        _option(expiry=datetime(2020, 1, 1, tzinfo=UTC)).require_execution_metadata()


def test_make_instrument_id_keeps_spot_and_derivatives_distinct() -> None:
    expiry = datetime(2026, 12, 31, 8, tzinfo=UTC)
    spot = make_instrument_id(AssetClass.CRYPTO, "binance", "BTCUSDT")
    perp = make_instrument_id(
        AssetClass.CRYPTO,
        "binance",
        "BTCUSDT",
        contract_type=ContractType.PERPETUAL,
    )
    option = make_instrument_id(
        AssetClass.OPTION,
        "deribit",
        "BTC",
        contract_type=ContractType.OPTION,
        expiry=expiry,
        strike=100_000.0,
        option_type="call",
    )
    assert len({spot, perp, option}) == 3
    assert "perpetual" in perp
    assert "K100000" in option
    assert option.endswith(":C")


def test_registry_is_idempotent_but_rejects_conflicts_and_provider_collisions() -> None:
    registry = InstrumentRegistry()
    first = registry.register(_equity())
    assert registry.register(_equity()) is first
    assert registry.require(first.instrument_id) is first
    assert registry.by_provider("alpaca", "AAPL") is first
    assert len(registry) == 1

    with pytest.raises(InstrumentConflictError, match="different metadata"):
        registry.register(_equity(exchange="NYSE"))
    with pytest.raises(InstrumentConflictError, match="provider symbol"):
        registry.register(
            _equity(
                instrument_id="equity:alpaca:AAPL-ALT",
                provider_id="alpaca",
                provider_symbol="AAPL",
            )
        )
    assert registry.version_hash() == InstrumentRegistry([first]).version_hash()


def test_timezone_and_metadata_version_are_validated() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _equity(timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="metadata_version"):
        _equity(metadata_version="")
    with pytest.raises(ValueError, match="unique"):
        _equity(extra=(("a", "1"), ("a", "2")))
    with pytest.raises(ValueError, match="positive"):
        _equity(tick_size=0.0)


def test_expiry_requires_timezone_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _option(expiry=datetime(2026, 12, 31, 8))
    assert _option(expiry=datetime.now(UTC) + timedelta(days=1)).is_expired() is False


def test_instrument_symbol_parses_contract_suffixes_without_confusing_them() -> None:
    expiry = datetime(2026, 12, 31, 8, tzinfo=UTC)
    option_id = make_instrument_id(
        AssetClass.OPTION,
        "deribit",
        "BTC-20261231-100000-C",
        contract_type=ContractType.OPTION,
        expiry=expiry,
        strike=100_000.0,
        option_type="call",
    )
    perpetual_id = make_instrument_id(
        AssetClass.CRYPTO,
        "binance",
        "BTCUSDT",
        contract_type=ContractType.PERPETUAL,
    )
    assert instrument_symbol(option_id) == "BTC-20261231-100000-C"
    assert instrument_symbol(perpetual_id) == "BTCUSDT"
    assert instrument_venue(option_id) == "deribit"
    assert instrument_venue(perpetual_id) == "binance"
    with pytest.raises(ValueError, match="instrument_id"):
        instrument_symbol("not-a-canonical-id")
