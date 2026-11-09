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
