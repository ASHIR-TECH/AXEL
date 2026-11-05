"""Tests for the pure-stdlib financial turbulence index."""

import pytest

from axel.risk.turbulence import (
    covariance_matrix,
    invert_matrix,
    is_turbulent,
    mean_vector,
    rolling_turbulence,
    turbulence_index,
)

_HISTORY = [
    [0.01, -0.01],
    [0.02, 0.00],
    [0.00, 0.02],
    [0.01, 0.01],
    [0.03, -0.02],
]


def test_mean_vector() -> None:
    assert mean_vector(_HISTORY) == pytest.approx([0.014, 0.0])


def test_covariance_matrix_is_symmetric_and_positive() -> None:
    covariance = covariance_matrix(_HISTORY)

    assert covariance[0][0] > 0
    assert covariance[1][1] > 0
    assert covariance[0][1] == pytest.approx(covariance[1][0])


def test_invert_matrix_roundtrip() -> None:
    matrix = [[4.0, 1.0], [1.0, 3.0]]

    inverse = invert_matrix(matrix)

    product = [
        [sum(matrix[i][k] * inverse[k][j] for k in range(2)) for j in range(2)]
        for i in range(2)
    ]
    assert [value for row in product for value in row] == pytest.approx([1.0, 0.0, 0.0, 1.0])


def test_invert_matrix_rejects_singular_and_rectangular() -> None:
    with pytest.raises(ValueError):
        invert_matrix([[1.0, 2.0], [2.0, 4.0]])
    with pytest.raises(ValueError):
        invert_matrix([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    with pytest.raises(ValueError):
        invert_matrix([])


def test_turbulence_at_the_mean_is_zero() -> None:
    mean = mean_vector(_HISTORY)

    assert turbulence_index(mean, _HISTORY) == pytest.approx(0.0, abs=1e-9)


def test_turbulence_grows_with_distance_from_history() -> None:
    near = turbulence_index([0.014, 0.0], _HISTORY)
    far = turbulence_index([0.5, -0.5], _HISTORY)

    assert near == pytest.approx(0.0, abs=1e-9)
    assert far > near
    assert far > 1.0


def test_turbulence_ridge_handles_collinear_history() -> None:
    history = [[0.01, 0.02], [0.02, 0.04], [0.03, 0.06], [0.04, 0.08]]

    with pytest.raises(ValueError):
        turbulence_index([0.05, 0.10], history)

    value = turbulence_index([0.05, 0.10], history, ridge=1e-6)
    assert value >= 0.0


def test_turbulence_validates_inputs() -> None:
    with pytest.raises(ValueError):
        turbulence_index([0.01], _HISTORY)
    with pytest.raises(ValueError):
        turbulence_index([0.01, 0.02], _HISTORY, ridge=-1.0)
    with pytest.raises(ValueError):
        covariance_matrix([[0.01, 0.02]])
    with pytest.raises(ValueError):
        covariance_matrix([[0.01], [0.02, 0.03]])
    with pytest.raises(ValueError):
        mean_vector([[]])


def test_rolling_turbulence_leaves_warmup_empty() -> None:
    returns = [[0.01, 0.02], [0.02, 0.01], [0.03, 0.0], [0.0, 0.03], [0.05, 0.05]]

    readings = rolling_turbulence(returns, window=3, min_periods=3, ridge=1e-9)

    assert len(readings) == len(returns)
    assert readings[:3] == (None, None, None)
    assert readings[3] is not None and readings[3] >= 0.0
    assert readings[4] is not None and readings[4] > 0.0


def test_rolling_turbulence_validates_arguments() -> None:
    with pytest.raises(ValueError):
        rolling_turbulence([[0.01]], window=2)
    with pytest.raises(ValueError):
        rolling_turbulence([[0.01], [0.02]], window=1)
    with pytest.raises(ValueError):
        rolling_turbulence([[0.01], [0.02]], min_periods=1)


def test_is_turbulent() -> None:
    assert is_turbulent(70.0, 70.0)
    assert is_turbulent(100.0, 70.0)
    assert not is_turbulent(69.99, 70.0)
    assert not is_turbulent(None, 70.0)
    assert not is_turbulent(float("nan"), 70.0)
    with pytest.raises(ValueError):
        is_turbulent(1.0, -1.0)
