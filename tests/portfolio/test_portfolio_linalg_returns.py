"""Deterministic linear algebra and return-estimation tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from support import NOW

from axel.portfolio.linalg import (
    QPStatus,
    is_positive_semidefinite,
    is_symmetric,
    jacobi_eigenvalues,
    minimize_qp,
    project_box_simplex,
    project_simplex,
)
from axel.portfolio.returns import (
    InvalidCovarianceError,
    PriceSeries,
    build_return_panel,
    complete_case_matrix,
    covariance_diagnostics,
    ewma_covariance,
    portfolio_volatility,
    sample_covariance,
    shrink_covariance,
    tracking_error,
)


def _series(instrument_id: str, prices: list[float], step_days: int = 1) -> PriceSeries:
    return PriceSeries(
        instrument_id=instrument_id,
        timestamps=tuple(NOW + timedelta(days=index * step_days) for index in range(len(prices))),
        prices=tuple(prices),
    )


def test_simplex_projection_is_feasible_and_idempotent() -> None:
    projected = project_simplex((0.8, -0.4, 0.9, 0.2))
    assert sum(projected) == pytest.approx(1.0, abs=1e-12)
    assert min(projected) >= 0.0
    assert project_simplex(projected) == pytest.approx(projected, abs=1e-12)


def test_box_projection_rejects_infeasible_target() -> None:
    with pytest.raises(ValueError, match="infeasible"):
        project_box_simplex((0.5, 0.5), lower=0.0, upper=0.4, target_sum=1.0)


def test_symmetric_psd_and_eigenvalues() -> None:
    covariance = [[0.04, 0.01], [0.01, 0.09]]
    assert is_symmetric(covariance)
    assert is_positive_semidefinite(covariance)
    eigenvalues = jacobi_eigenvalues(covariance)
    assert eigenvalues[0] > 0
    assert sum(eigenvalues) == pytest.approx(0.13, abs=1e-10)
    assert not is_positive_semidefinite([[1.0, 2.0], [2.0, 1.0]])


def test_qp_minimum_variance_solution() -> None:
    result = minimize_qp(
        [[0.04, 0.01], [0.01, 0.09]],
        [0.0, 0.0],
        max_iterations=5_000,
        tolerance=1e-8,
    )
    assert result.status in {QPStatus.OPTIMAL, QPStatus.APPROXIMATE}
    assert sum(result.x) == pytest.approx(1.0, abs=1e-8)
    assert result.x[0] == pytest.approx(0.727272727, abs=5e-3)
    assert result.x[1] == pytest.approx(0.272727273, abs=5e-3)


def test_qp_rejects_non_psd_matrix() -> None:
    result = minimize_qp([[1.0, 2.0], [2.0, 1.0]], [0.0, 0.0])
    assert result.status == QPStatus.INVALID_INPUT
    assert "positive semidefinite" in result.message


def test_return_panel_preserves_asynchronous_gaps() -> None:
    panel = build_return_panel(
        (
            _series("a", [100.0, 101.0, 102.0, 103.0]),
            _series("b", [200.0, 202.0, 204.0, 206.0], step_days=2),
        ),
        min_coverage=0.5,
    )
    assert panel.instrument_ids == ("a", "b")
    assert len(panel.timestamps) == 5
    assert panel.coverage["a"] == pytest.approx(3 / 5)
    assert panel.coverage["b"] == pytest.approx(3 / 5)
    selected, timestamps, rows = complete_case_matrix(panel, min_observations=1)
    assert selected == ("a", "b")
    assert len(timestamps) == len(rows)
    assert all(len(row) == 2 for row in rows)


def test_sample_covariance_is_symmetric_and_psd() -> None:
    panel = build_return_panel(
        (
            _series("a", [100.0, 101.0, 100.0, 102.0, 103.0, 102.0]),
            _series("b", [50.0, 50.5, 50.2, 51.0, 51.5, 51.1]),
        )
    )
    result = sample_covariance(panel)
    assert result.instrument_ids == ("a", "b")
    diagnostics = covariance_diagnostics(result.covariance)
    assert diagnostics.is_valid
    assert diagnostics.positive_semidefinite
    assert portfolio_volatility((0.5, 0.5), result.covariance) > 0


def test_ewma_and_shrinkage_stay_psd() -> None:
    panel = build_return_panel(
        (
            _series("a", [100.0 + index * 0.5 for index in range(20)]),
            _series("b", [50.0 + (index % 3) * 0.2 for index in range(20)]),
        )
    )
    ewma = ewma_covariance(panel, decay=0.9)
    assert covariance_diagnostics(ewma.covariance).is_valid
    shrunk = shrink_covariance(ewma.covariance, target="constant_correlation", shrinkage=0.25)
    assert covariance_diagnostics(shrunk.covariance).is_valid
    assert shrunk.shrinkage == pytest.approx(0.25)


def test_covariance_diagnostics_reject_asymmetric_matrix() -> None:
    diagnostics = covariance_diagnostics([[1.0, 0.2], [0.1, 1.0]])
    assert not diagnostics.is_valid
    assert not diagnostics.symmetric
    with pytest.raises(InvalidCovarianceError):
        shrink_covariance([[1.0, 0.2], [0.1, 1.0]], target="diagonal", shrinkage=0.1)


def test_tracking_error_zero_for_identical_portfolio() -> None:
    assert tracking_error((0.0, 0.0, 0.0)) == pytest.approx(0.0)
    assert tracking_error((0.01, -0.01, 0.01)) > 0.0
