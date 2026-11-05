"""Tests for the deterministic equal-risk-contribution solver."""

import pytest

from axel.portfolio.risk import risk_contribution_percentages
from axel.portfolio.risk_parity import (
    equal_risk_contribution,
    solve_equal_risk_contribution,
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
    result = solve_equal_risk_contribution([[0.04]])

    assert result.weights == (1.0,)
    assert result.risk_contributions == (1.0,)
    assert result.converged
    assert result.iterations == 0


def test_diagonal_covariance_reduces_to_inverse_volatility() -> None:
    weights = equal_risk_contribution([[0.04, 0.0], [0.0, 0.01]])

    assert weights == pytest.approx((1 / 3, 2 / 3))


def test_correlated_assets_get_equal_risk_contributions() -> None:
    covariance = _covariance(
        [0.2, 0.1, 0.4],
        [
            [1.0, 0.5, 0.1],
            [0.5, 1.0, 0.2],
            [0.1, 0.2, 1.0],
        ],
    )

    result = solve_equal_risk_contribution(covariance)

    assert result.converged
    assert sum(result.weights) == pytest.approx(1.0)
    assert all(weight > 0.0 for weight in result.weights)
    assert result.risk_contributions == pytest.approx((1 / 3, 1 / 3, 1 / 3), abs=1e-8)
    assert result.risk_contributions == pytest.approx(
        risk_contribution_percentages(result.weights, covariance), abs=1e-12
    )


def test_weights_are_long_only_and_fully_invested() -> None:
    covariance = _covariance(
        [0.3, 0.2, 0.25, 0.15],
        [
            [1.0, 0.7, 0.2, 0.0],
            [0.7, 1.0, 0.3, 0.1],
            [0.2, 0.3, 1.0, 0.6],
            [0.0, 0.1, 0.6, 1.0],
        ],
    )

    weights = equal_risk_contribution(covariance)

    assert all(weight > 0.0 for weight in weights)
    assert sum(weights) == pytest.approx(1.0)
    assert max(risk_contribution_percentages(weights, covariance)) - min(
        risk_contribution_percentages(weights, covariance)
    ) == pytest.approx(0.0, abs=1e-8)


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

    base = equal_risk_contribution(covariance)
    moved = equal_risk_contribution(permuted)

    assert [moved[permutation.index(index)] for index in range(3)] == pytest.approx(base)


def test_solver_reports_iterations() -> None:
    result = solve_equal_risk_contribution([[0.04, 0.01], [0.01, 0.09]])

    assert result.iterations > 0
    assert result.converged


def test_validation_errors() -> None:
    with pytest.raises(ValueError):
        equal_risk_contribution([])
    with pytest.raises(ValueError):
        equal_risk_contribution([[0.04, 0.01, 0.0], [0.01, 0.09, 0.0]])
    with pytest.raises(ValueError):
        equal_risk_contribution([[0.04, 0.01], [0.0, 0.09]])
    with pytest.raises(ValueError):
        equal_risk_contribution([[0.0, 0.0], [0.0, 0.09]])
    with pytest.raises(ValueError):
        equal_risk_contribution([[float("nan"), 0.0], [0.0, 0.09]])
    with pytest.raises(ValueError):
        solve_equal_risk_contribution([[0.04]], max_iterations=0)
    with pytest.raises(ValueError):
        solve_equal_risk_contribution([[0.04]], tolerance=0.0)


def test_flat_covariance_stays_deterministic() -> None:
    covariance = [[0.04, 0.04], [0.04, 0.04]]

    first = equal_risk_contribution(covariance)
    second = equal_risk_contribution(covariance)

    assert first == second
    assert first == pytest.approx((0.5, 0.5))
