"""Tests for Alpaca credential resolution used by the data-key rotation."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from axel.core.config import AxelSettings


def _settings(**kwargs) -> AxelSettings:
    return AxelSettings(_env_file=None, **kwargs)


def test_alpaca_credentials_collects_primary_and_secondary() -> None:
    settings = _settings(
        alpaca_api_key=SecretStr("primary"),
        alpaca_secret_key=SecretStr("primary-secret"),
        alpaca_api_key_2=SecretStr("secondary"),
        alpaca_secret_key_2=SecretStr("secondary-secret"),
    )
    assert settings.alpaca_credentials() == [
        ("primary", "primary-secret"),
        ("secondary", "secondary-secret"),
    ]


def test_alpaca_credentials_reads_combined_pairs_and_deduplicates() -> None:
    settings = _settings(
        alpaca_api_key=SecretStr("primary"),
        alpaca_secret_key=SecretStr("primary-secret"),
        alpaca_api_keys=SecretStr("primary:ignored, third:third-secret"),
    )
    assert settings.alpaca_credentials() == [
        ("primary", "primary-secret"),
        ("third", "third-secret"),
    ]


def test_alpaca_credentials_skips_incomplete_pairs() -> None:
    settings = _settings(alpaca_api_key=SecretStr("primary"))
    assert settings.alpaca_credentials() == []


def test_alpaca_credentials_rejects_malformed_combined_entry() -> None:
    settings = _settings(alpaca_api_keys=SecretStr("not-a-pair"))
    with pytest.raises(ValueError):
        settings.alpaca_credentials()


def test_equibles_credentials_repeats_key_into_the_secret_slot() -> None:
    settings = _settings(
        equibles_api_key=SecretStr("eq_primary"),
        equibles_api_key_2=SecretStr("eq_secondary"),
    )
    assert settings.equibles_credentials() == [
        ("eq_primary", "eq_primary"),
        ("eq_secondary", "eq_secondary"),
    ]


def test_equibles_credentials_reads_combined_keys_and_deduplicates() -> None:
    settings = _settings(
        equibles_api_key=SecretStr("eq_primary"),
        equibles_api_keys=SecretStr("eq_primary:eq_primary, eq_third:eq_third"),
    )
    assert settings.equibles_credentials() == [
        ("eq_primary", "eq_primary"),
        ("eq_third", "eq_third"),
    ]


def test_equibles_credentials_empty_without_key() -> None:
    assert _settings().equibles_credentials() == []


def test_massive_and_equibles_settings_have_defaults() -> None:
    settings = _settings(massive_api_key=SecretStr("massive-key"))
    assert settings.massive_api_key is not None
    assert settings.massive_base_url == "https://api.massive.com"
    assert settings.equibles_key_strategy == "failover"
    assert settings.equibles_key_cooldown_seconds == 60.0
    assert settings.equibles_base_url == "https://api.equibles.com/v1"


def test_twelve_data_and_finnhub_settings_have_defaults() -> None:
    settings = _settings(
        twelve_data_api_key=SecretStr("td-key"),
        finnhub_api_key=SecretStr("fh-key"),
    )
    assert settings.twelve_data_api_key is not None
    assert settings.twelve_data_base_url == "https://api.twelvedata.com"
    assert settings.finnhub_api_key is not None
    assert settings.finnhub_base_url == "https://finnhub.io/api/v1"


