"""Tests for deterministic Hierarchical Risk Parity allocation."""

import pytest

from axel.portfolio.hrp import (
    cluster_variance,
    correlation_from_covariance,
    distance_matrix,
    hierarchical_risk_parity,
    hrp_weights,
)


def _covariance(volatilities: list[float], correlations: list[list[float]]) -> list[list[float]]:
    return [
        [
            volatilities[i] * volatilities[j] * correlations[i][j]
            for j in range(len(volatilities))
        ]
        for i in range(len(volatilities))
    ]


def test_single_asset_gets_full_weight() -> None:
    assert hrp_weights([[0.04]]) == (1.0,)


def test_two_uncorrelated_assets_get_inverse_variance_weights() -> None:
    covariance = [[0.04, 0.0], [0.0, 0.01]]

    weights = hrp_weights(covariance)

    assert weights == pytest.approx((0.2, 0.8))
    assert sum(weights) == pytest.approx(1.0)


def test_identical_assets_get_equal_weights() -> None:
    covariance = [[0.04, 0.04], [0.04, 0.04]]

    weights = hrp_weights(covariance)

    assert weights == pytest.approx((0.5, 0.5))


def test_weights_are_long_only_and_fully_invested() -> None:
    covariance = _covariance(
        [0.2, 0.1, 0.3, 0.15],
        [
            [1.0, 0.8, 0.1, 0.0],
            [0.8, 1.0, 0.2, 0.1],
            [0.1, 0.2, 1.0, 0.7],
            [0.0, 0.1, 0.7, 1.0],
        ],
    )

    weights = hrp_weights(covariance)

    assert len(weights) == 4
    assert all(weight >= 0.0 for weight in weights)
    assert sum(weights) == pytest.approx(1.0)


def test_correlated_cluster_receives_less_capital_than_equal_split() -> None:
    # Two assets are near-duplicates (high correlation), the third is independent.
    covariance = _covariance(
        [0.2, 0.2, 0.2],
        [
            [1.0, 0.95, 0.0],
            [0.95, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
    )

    weights = hrp_weights(covariance)

    # The duplicate pair shares one cluster allocation instead of receiving
    # twice the capital of the independent asset.
    assert weights[0] < weights[2]
    assert weights[1] < weights[2]
    assert sum(weights[:2]) == pytest.approx(weights[2], abs=0.05)


def test_permuting_inputs_permutes_weights() -> None:
    covariance = _covariance(
        [0.2, 0.1, 0.3],
        [
            [1.0, 0.6, 0.2],
            [0.6, 1.0, 0.1],
            [0.2, 0.1, 1.0],
        ],
    )
    permutation = [2, 0, 1]
    permuted = [[covariance[i][j] for j in permutation] for i in permutation]

    base = hrp_weights(covariance)
    moved = hrp_weights(permuted)

    assert [moved[permutation.index(index)] for index in range(3)] == pytest.approx(base)


def test_correlation_and_distance_transforms() -> None:
    covariance = [[0.04, 0.03], [0.03, 0.09]]

    correlation = correlation_from_covariance(covariance)
    distances = distance_matrix(correlation)

    assert correlation[0][1] == pytest.approx(0.5)
    assert correlation[0][0] == pytest.approx(1.0)
    assert distances[0][0] == pytest.approx(0.0)
    assert distances[0][1] == pytest.approx(0.5)
    assert distances[0][1] == distances[1][0]


def test_cluster_variance_matches_direct_quadratic_form() -> None:
    covariance = [[0.04, 0.01], [0.01, 0.09]]
    indices = [0, 1]

    # Inverse-variance weights: (1/0.04, 1/0.09) normalised.
    w0 = (1 / 0.04) / (1 / 0.04 + 1 / 0.09)
    w1 = 1.0 - w0
    expected = (
        w0 * w0 * covariance[0][0]
        + 2 * w0 * w1 * covariance[0][1]
        + w1 * w1 * covariance[1][1]
    )

    assert cluster_variance(covariance, indices) == pytest.approx(expected)


def test_result_carries_labels_order_and_linkage() -> None:
    covariance = [[0.04, 0.0], [0.0, 0.01]]

    result = hierarchical_risk_parity(covariance, instrument_ids=["BTC", "ETH"])

    assert result.instrument_ids == ("BTC", "ETH")
    assert result.weights == pytest.approx((0.2, 0.8))
    assert sorted(result.order) == [0, 1]
    assert len(result.linkage) == 1


def test_default_labels_are_indices() -> None:
    result = hierarchical_risk_parity([[0.04, 0.0], [0.0, 0.01]])

    assert result.instrument_ids == ("0", "1")


def test_validation_errors() -> None:
    with pytest.raises(ValueError):
        hrp_weights([])
    with pytest.raises(ValueError):
        hrp_weights([[0.04, 0.0, 0.0], [0.0, 0.01, 0.0]])
    with pytest.raises(ValueError):
        hrp_weights([[0.04, 0.01], [0.0, 0.01]])
    with pytest.raises(ValueError):
        hrp_weights([[0.0, 0.0], [0.0, 0.01]])
    with pytest.raises(ValueError):
        hrp_weights([[float("nan"), 0.0], [0.0, 0.01]])
    with pytest.raises(ValueError):
        hierarchical_risk_parity([[0.04, 0.0], [0.0, 0.01]], instrument_ids=["BTC"])
    with pytest.raises(ValueError):
        cluster_variance([[0.04, 0.0], [0.0, 0.01]], [])
