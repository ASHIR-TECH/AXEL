"""Unit tests for Feature Registry and immutability protection."""

from __future__ import annotations

import pytest

from axel.research.feature import (
    FeatureDefinition,
    FeatureRegistry,
    FeatureVersion,
)


def test_feature_registration_and_versioning():
    registry = FeatureRegistry()
    fv1 = FeatureVersion(
        feature_id="volatility_20d",
        version="v1",
        description="20-day realized volatility",
        required_lookback=20,
        applicable_sections=["stocks", "crypto"],
        applicable_instruments=["SPY", "QQQ"],
    )
    fdef = FeatureDefinition(feature_id="volatility_20d", current_version="v1", versions={"v1": fv1})
    registry.register(fdef)

    assert registry.get("volatility_20d") is not None
    assert registry.get_version("volatility_20d", "v1").required_lookback == 20


def test_immutable_feature_cannot_be_overwritten():
    fv1 = FeatureVersion(
        feature_id="momentum_10d",
        version="v1",
        description="Original v1",
        immutable=True,
    )
    fdef = FeatureDefinition(feature_id="momentum_10d", current_version="v1", versions={"v1": fv1})

    tampered_fv1 = FeatureVersion(
        feature_id="momentum_10d",
        version="v1",
        description="Tampered v1",
        immutable=False,
    )

    with pytest.raises(ValueError, match="locked/immutable and cannot be overwritten"):
        fdef.add_version(tampered_fv1)


def test_mark_immutable():
    fv1 = FeatureVersion(feature_id="rsi_14", version="v1", immutable=False)
    fdef = FeatureDefinition(feature_id="rsi_14", current_version="v1", versions={"v1": fv1})
    assert fdef.get("v1").immutable is False

    locked_fdef = fdef.mark_immutable("v1")
    assert locked_fdef.get("v1").immutable is True
