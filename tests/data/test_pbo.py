import itertools

import pytest

from axel.validation.pbo import (
    PBO_VERSION,
    calculate_pbo,
    cscv_block_combinations,
    make_partitions,
)


def _dominant_matrix(periods: int = 64, strategies: int = 5) -> list[list[float]]:
    rows = []
    for index in range(periods):
        row = [0.01 + 0.0001 * index]
        row.extend(-0.002 + 0.00001 * index * strategy for strategy in range(1, strategies))
        rows.append(row)
    return rows


def _alternating_matrix(periods: int = 64) -> list[list[float]]:
    """Two strategies whose performance alternates by block.

    The IS winner tends to be the OOS loser whenever a split is unbalanced,
    which is the selection-bias pattern PBO is designed to expose.
    """
    rows = []
    for index in range(periods):
        if index < periods // 2:
            rows.append([0.02, -0.02, 0.0001])
        else:
            rows.append([-0.02, 0.02, 0.0001])
    return rows


def test_partitions_cover_the_timeline_without_gaps() -> None:
    partitions = make_partitions(100, 8)
    assert partitions[0].start == 0
    assert partitions[-1].end == 100
    assert sum(part.size for part in partitions) == 100
    assert all(
        left.end == right.start for left, right in itertools.pairwise(partitions)
    )


def test_cscv_enumerates_every_split_and_guards_explosion() -> None:
    splits = list(cscv_block_combinations(8))
    assert len(splits) == 70
    assert all(len(split) == 4 for split in splits)
    with pytest.raises(ValueError, match="max_splits"):
        list(cscv_block_combinations(20, max_splits=10))


def test_dominant_strategy_has_zero_pbo() -> None:
    report = calculate_pbo(
        _dominant_matrix(),
        experiment_id="exp-dominant",
        n_partitions=8,
    )
    assert report.pbo == 0.0
    assert report.n_splits == 70
    assert report.n_strategies == 5
    assert report.calculation_version == PBO_VERSION
    assert all(split.selected_strategy == 0 for split in report.splits)


def test_selection_bias_raises_pbo_and_report_preserves_lineage() -> None:
    report = calculate_pbo(
        _alternating_matrix(),
        experiment_id="exp-alternating",
        strategy_ids=("first-half", "second-half", "flat"),
        n_partitions=8,
    )
    assert 0.0 < report.pbo < 1.0
    assert report.pbo > 0.25
    assert report.returns_hash
    assert report.strategy_ids == ("first-half", "second-half", "flat")
    assert len(report.partitions) == 8
    assert len(report.splits) == report.n_splits
    assert all(
        len(split.in_sample_ranking) == 3 and len(split.out_of_sample_ranking) == 3
        for split in report.splits
    )
    assert report.to_dict()["pbo"] == report.pbo
    assert report.to_dict(include_splits=False).get("splits") is None


def test_pbo_is_deterministic_and_hash_tracks_inputs() -> None:
    first = calculate_pbo(_alternating_matrix(), experiment_id="exp", n_partitions=8)
    second = calculate_pbo(_alternating_matrix(), experiment_id="exp", n_partitions=8)
    assert first.pbo == second.pbo
    assert first.returns_hash == second.returns_hash
    changed = _alternating_matrix()
    changed[0][0] = 0.03
    third = calculate_pbo(changed, experiment_id="exp", n_partitions=8)
    assert third.returns_hash != first.returns_hash


def test_invalid_inputs_fail_closed() -> None:
    with pytest.raises(ValueError, match="experiment_id"):
        calculate_pbo(_dominant_matrix(), experiment_id="")
    with pytest.raises(ValueError, match="at least two strategies"):
        calculate_pbo([[0.1], [0.2], [0.3], [0.4]], experiment_id="exp")
    with pytest.raises(ValueError, match="expected 2"):
        calculate_pbo(
            [[0.1, 0.2], [0.3], [0.2, 0.3], [0.2, 0.3], [0.2, 0.3]],
            experiment_id="exp",
        )
    with pytest.raises(ValueError, match="finite"):
        calculate_pbo(
            [[0.1, float("nan")], [0.2, 0.3], [0.2, 0.3], [0.2, 0.3]],
            experiment_id="exp",
        )
    with pytest.raises(ValueError, match="even number"):
        calculate_pbo(_dominant_matrix(), experiment_id="exp", n_partitions=7)
    with pytest.raises(ValueError, match="strategy_ids"):
        calculate_pbo(
            _dominant_matrix(),
            experiment_id="exp",
            strategy_ids=("one",),
        )
