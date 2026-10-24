"""
Small, dependency-free linear algebra for Phase 11.

AXEL's runtime dependencies do not include NumPy, so portfolio analytics use
explicit, deterministic pure-Python routines. Dimensions are intentionally
small (sections and eligible instruments), and every solver failure is returned
as a typed status rather than silently falling back to an unconstrained answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from math import copysign, isfinite, sqrt


class QPStatus(str, Enum):
    OPTIMAL = "optimal"
    APPROXIMATE = "approximate"
    INFEASIBLE = "infeasible"
    INVALID_INPUT = "invalid_input"
    MAX_ITERATIONS = "max_iterations"


@dataclass(frozen=True)
class QPResult:
    status: QPStatus
    x: tuple[float, ...]
    iterations: int
    message: str


def _vector(values: Sequence[float], name: str = "vector") -> list[float]:
    result = [float(value) for value in values]
    if not result:
        raise ValueError(f"{name} must be non-empty")
    for value in result:
        if not isfinite(value):
            raise ValueError(f"{name} contains a non-finite value")
    return result


def _matrix(matrix: Sequence[Sequence[float]], name: str = "matrix") -> list[list[float]]:
    rows = [list(row) for row in matrix]
    if not rows:
        raise ValueError(f"{name} must be non-empty")
    width = len(rows[0])
    if width == 0 or any(len(row) != width for row in rows):
        raise ValueError(f"{name} must be rectangular")
    for row in rows:
        for value in row:
            if not isfinite(value):
                raise ValueError(f"{name} contains a non-finite value")
    return rows


def shape(matrix: Sequence[Sequence[float]]) -> tuple[int, int]:
    rows = _matrix(matrix)
    return len(rows), len(rows[0])


def dot(left: Sequence[float], right: Sequence[float]) -> float:
    a = _vector(left, "left")
    b = _vector(right, "right")
    if len(a) != len(b):
        raise ValueError("dot product dimension mismatch")
    return sum(x * y for x, y in zip(a, b, strict=True))


def mat_vec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    rows = _matrix(matrix)
    vec = _vector(vector)
    if len(rows[0]) != len(vec):
        raise ValueError("matrix/vector dimension mismatch")
    return [sum(value * vec[j] for j, value in enumerate(row)) for row in rows]


def mat_mat(
    left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]
) -> list[list[float]]:
    a = _matrix(left, "left")
    b = _matrix(right, "right")
    if len(a[0]) != len(b):
        raise ValueError("matrix multiplication dimension mismatch")
    result = [[0.0] * len(b[0]) for _ in range(len(a))]
    for i in range(len(a)):
        for k in range(len(b)):
            aik = a[i][k]
            if aik == 0.0:
                continue
            for j in range(len(b[0])):
                result[i][j] += aik * b[k][j]
    return result


def transpose(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    rows = _matrix(matrix)
    return [[rows[i][j] for i in range(len(rows))] for j in range(len(rows[0]))]


def outer(left: Sequence[float], right: Sequence[float]) -> list[list[float]]:
    a = _vector(left, "left")
    b = _vector(right, "right")
    return [[x * y for y in b] for x in a]


def identity(size: int) -> list[list[float]]:
    if size < 1:
        raise ValueError("identity size must be >= 1")
    return [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]


def scale(matrix: Sequence[Sequence[float]], factor: float) -> list[list[float]]:
    if not isfinite(factor):
        raise ValueError("scale factor must be finite")
    return [[value * factor for value in row] for row in _matrix(matrix)]


def add(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> list[list[float]]:
    a = _matrix(left, "left")
    b = _matrix(right, "right")
    if len(a) != len(b) or len(a[0]) != len(b[0]):
        raise ValueError("matrix addition dimension mismatch")
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def quadratic_form(weights: Sequence[float], covariance: Sequence[Sequence[float]]) -> float:
    vec = _vector(weights)
    product = mat_vec(covariance, vec)
    return dot(vec, product)


def is_symmetric(matrix: Sequence[Sequence[float]], tolerance: float = 1e-9) -> bool:
    rows = _matrix(matrix)
    if len(rows) != len(rows[0]):
        return False
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            if abs(rows[i][j] - rows[j][i]) > tolerance:
                return False
    return True


def jacobi_eigenvalues(
    matrix: Sequence[Sequence[float]],
    *,
    max_iterations: int = 100,
    tolerance: float = 1e-12,
) -> list[float]:
    """Eigenvalues of a real symmetric matrix via cyclic Jacobi rotations."""
    rows = _matrix(matrix)
    if len(rows) != len(rows[0]):
        raise ValueError("eigenvalues require a square matrix")
    if not is_symmetric(rows, tolerance=max(tolerance, 1e-12)):
        raise ValueError("eigenvalues require a symmetric matrix")

    size = len(rows)
    work = [row[:] for row in rows]
    for _ in range(max_iterations):
        off_diagonal = sqrt(
            sum(work[i][j] * work[i][j] for i in range(size) for j in range(size) if i != j)
        )
        if off_diagonal <= tolerance:
            break
        for p in range(size - 1):
            for q in range(p + 1, size):
                apq = work[p][q]
                if abs(apq) <= tolerance:
                    continue
                tau = (work[q][q] - work[p][p]) / (2.0 * apq)
                if tau == 0.0:
                    tangent = 1.0
                else:
                    tangent = copysign(1.0, tau) / (abs(tau) + sqrt(1.0 + tau * tau))
                cosine = 1.0 / sqrt(1.0 + tangent * tangent)
                sine = tangent * cosine

                app = work[p][p]
                aqq = work[q][q]
                work[p][p] = app - tangent * apq
                work[q][q] = aqq + tangent * apq
                work[p][q] = 0.0
                work[q][p] = 0.0
                for index in range(size):
                    if index in (p, q):
                        continue
                    aip = work[index][p]
                    aiq = work[index][q]
                    work[index][p] = cosine * aip - sine * aiq
                    work[p][index] = work[index][p]
                    work[index][q] = sine * aip + cosine * aiq
                    work[q][index] = work[index][q]
    return sorted(work[i][i] for i in range(size))


def min_eigenvalue(matrix: Sequence[Sequence[float]]) -> float:
    return jacobi_eigenvalues(matrix)[0]


def max_eigenvalue(matrix: Sequence[Sequence[float]]) -> float:
    return jacobi_eigenvalues(matrix)[-1]


def is_positive_semidefinite(
    matrix: Sequence[Sequence[float]], tolerance: float = 1e-8
) -> bool:
    rows = _matrix(matrix)
    if len(rows) != len(rows[0]):
        return False
    if not is_symmetric(rows):
        return False
    return min_eigenvalue(rows) >= -tolerance


def condition_number(matrix: Sequence[Sequence[float]], tolerance: float = 1e-12) -> float:
    eigenvalues = jacobi_eigenvalues(matrix)
    smallest = max(abs(eigenvalues[0]), tolerance)
    largest = max(abs(eigenvalues[-1]), tolerance)
    return largest / smallest


def project_simplex(values: Sequence[float]) -> list[float]:
    """Euclidean projection onto the probability simplex."""
    return project_box_simplex(values, 0.0, 1.0, 1.0)


def project_box_simplex(
    values: Sequence[float],
    lower: float,
    upper: float,
    target_sum: float = 1.0,
    *,
    tolerance: float = 1e-12,
) -> list[float]:
    """Project onto ``{x : lower <= x_i <= upper, sum(x) = target_sum}``.

    Uses bisection on the scalar Lagrange multiplier. The feasible set must be
    non-empty; otherwise ``ValueError`` is raised so callers can return an
    explicit INFEASIBLE status.
    """
    vec = _vector(values)
    if not isfinite(lower) or not isfinite(upper) or not isfinite(target_sum):
        raise ValueError("projection bounds must be finite")
    if lower > upper:
        raise ValueError("lower bound cannot exceed upper bound")
    size = len(vec)
    if size * lower - tolerance > target_sum or size * upper + tolerance < target_sum:
        raise ValueError("infeasible box/simplex projection")

    low = min(value - upper for value in vec) - 1.0
    high = max(value - lower for value in vec) + 1.0
    projected = [0.0] * size
    for _ in range(100):
        midpoint = (low + high) / 2.0
        total = 0.0
        for index, value in enumerate(vec):
            clipped = min(upper, max(lower, value - midpoint))
            projected[index] = clipped
            total += clipped
        if abs(total - target_sum) <= tolerance:
            break
        if total > target_sum:
            low = midpoint
        else:
            high = midpoint

    # Remove residual numerical drift without violating bounds.
    residual = target_sum - sum(projected)
    if abs(residual) > tolerance:
        for index in range(size):
            candidate = projected[index] + residual
            if lower - tolerance <= candidate <= upper + tolerance:
                projected[index] = min(upper, max(lower, candidate))
                break
    return projected


def minimize_qp(
    p_matrix: Sequence[Sequence[float]],
    q_vector: Sequence[float],
    *,
    lower: float = 0.0,
    upper: float = 1.0,
    target_sum: float = 1.0,
    max_iterations: int = 500,
    tolerance: float = 1e-8,
    initial: Sequence[float] | None = None,
) -> QPResult:
    """Minimize ``0.5 x'Px + q'x`` over a box + sum constraint.

    Projected gradient descent with Armijo backtracking. The objective is
    convex when ``P`` is positive semidefinite; the caller is responsible for
    validating that and for independently re-checking all policy constraints
    afterwards.
    """
    try:
        matrix = _matrix(p_matrix, "P")
        linear = _vector(q_vector, "q")
    except ValueError as exc:
        return QPResult(QPStatus.INVALID_INPUT, (), 0, str(exc))
    size = len(linear)
    if len(matrix) != size or len(matrix[0]) != size:
        return QPResult(QPStatus.INVALID_INPUT, (), 0, "P and q dimensions differ")
    if not is_symmetric(matrix):
        return QPResult(QPStatus.INVALID_INPUT, (), 0, "P must be symmetric")
    if not is_positive_semidefinite(matrix):
        return QPResult(QPStatus.INVALID_INPUT, (), 0, "P must be positive semidefinite")
    if max_iterations < 1:
        return QPResult(QPStatus.INVALID_INPUT, (), 0, "max_iterations must be >= 1")

    start = list(initial) if initial is not None else [target_sum / size] * size
    try:
        x = project_box_simplex(start, lower, upper, target_sum)
    except ValueError as exc:
        return QPResult(QPStatus.INFEASIBLE, (), 0, str(exc))

    def objective(point: Sequence[float]) -> float:
        return 0.5 * quadratic_form(point, matrix) + dot(linear, point)

    current = objective(x)
    iterations = 0
    for iterations in range(1, max_iterations + 1):
        gradient = [value + linear[i] for i, value in enumerate(mat_vec(matrix, x))]
        try:
            projected = project_box_simplex(
                [x[i] - gradient[i] for i in range(size)], lower, upper, target_sum
            )
        except ValueError as exc:
            return QPResult(QPStatus.INFEASIBLE, tuple(x), iterations, str(exc))
        direction = [projected[i] - x[i] for i in range(size)]
        if max(abs(value) for value in direction) <= tolerance:
            return QPResult(QPStatus.OPTIMAL, tuple(x), iterations, "converged")

        step = 1.0
        accepted = False
        while step > 1e-14:
            candidate = [
                min(upper, max(lower, x[i] - step * gradient[i])) for i in range(size)
            ]
            try:
                candidate = project_box_simplex(candidate, lower, upper, target_sum)
            except ValueError:
                break
            candidate_objective = objective(candidate)
            directional_derivative = dot(gradient, [candidate[i] - x[i] for i in range(size)])
            if candidate_objective <= current + 1e-4 * directional_derivative:
                x = candidate
                current = candidate_objective
                accepted = True
                break
            step *= 0.5
        if not accepted:
            return QPResult(
                QPStatus.APPROXIMATE,
                tuple(x),
                iterations,
                "line search could not make progress",
            )
    return QPResult(QPStatus.MAX_ITERATIONS, tuple(x), iterations, "iteration limit reached")


__all__ = [
    "QPResult",
    "QPStatus",
    "add",
    "condition_number",
    "dot",
    "identity",
    "is_positive_semidefinite",
    "is_symmetric",
    "jacobi_eigenvalues",
    "mat_mat",
    "mat_vec",
    "max_eigenvalue",
    "min_eigenvalue",
    "minimize_qp",
    "outer",
    "project_box_simplex",
    "project_simplex",
    "quadratic_form",
    "scale",
    "shape",
    "transpose",
]
