"""
Combinatorially Symmetric Cross-Validation (CSCV) and Probability of Backtest
Overfitting (PBO).

Reference: Bailey, Borwein, Lopez de Prado and Zhu (2017), "The Probability of
Backtest Overfitting", Journal of Computational Finance, DOI 10.21314/JCF.2016.322.

The procedure splits the return history into an even number ``S`` of contiguous
blocks, then evaluates every way of choosing ``S/2`` blocks as in-sample (IS) and
the remaining blocks as out-of-sample (OOS). For each split:

1. Rank all strategies by IS Sharpe.
2. Select the IS winner.
3. Rank that strategy's OOS Sharpe among all strategies.
4. Record whether it landed in the bottom half (an overfitting event).

``PBO`` is the fraction of splits in which the IS winner was a bottom-half OOS
performer. A high PBO means the selection process is not trustworthy, even if
the winning backtest looks excellent.

This implementation is exact (it enumerates all splits) and stores the
experiment identity, strategy identities, partition definitions, per-split
rankings and a hash of the return matrix so a report cannot be detached from
its inputs.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
from math import comb, isfinite, log, sqrt

PBO_VERSION = "cscv-1"
DEFAULT_MAX_SPLITS = 200_000


@dataclass(frozen=True, slots=True)
class PBOPartition:
    index: int
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class PBOSplit:
    split_index: int
    in_sample_blocks: tuple[int, ...]
    out_of_sample_blocks: tuple[int, ...]
    in_sample_sharpe: tuple[float, ...]
    out_of_sample_sharpe: tuple[float, ...]
    in_sample_ranking: tuple[int, ...]
    out_of_sample_ranking: tuple[int, ...]
    selected_strategy: int
    selected_oos_rank: int
    selected_oos_percentile: float
    logit: float
    overfit: bool


@dataclass(frozen=True, slots=True)
class PBOReport:
    experiment_id: str
    pbo: float
    n_splits: int
    n_strategies: int
    n_observations: int
    n_partitions: int
    calculation_version: str
    returns_hash: str
    strategy_ids: tuple[str, ...]
    partitions: tuple[PBOPartition, ...]
    splits: tuple[PBOSplit, ...]
    mean_selected_is_sharpe: float
    mean_selected_oos_sharpe: float
    mean_all_is_sharpe: float
    mean_all_oos_sharpe: float
    mean_oos_rank_correlation: float | None
    warnings: tuple[str, ...] = ()

    @property
    def performance_decay(self) -> float:
        return self.mean_selected_oos_sharpe - self.mean_selected_is_sharpe

    def is_acceptable(self, threshold: float = 0.5) -> bool:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be in [0, 1]")
        return self.pbo <= threshold

    def to_dict(self, *, include_splits: bool = True) -> dict:
        payload = {
            "experiment_id": self.experiment_id,
            "pbo": self.pbo,
            "n_splits": self.n_splits,
            "n_strategies": self.n_strategies,
            "n_observations": self.n_observations,
            "n_partitions": self.n_partitions,
            "calculation_version": self.calculation_version,
            "returns_hash": self.returns_hash,
            "strategy_ids": list(self.strategy_ids),
            "partitions": [
                {"index": part.index, "start": part.start, "end": part.end}
                for part in self.partitions
            ],
            "mean_selected_is_sharpe": self.mean_selected_is_sharpe,
            "mean_selected_oos_sharpe": self.mean_selected_oos_sharpe,
            "mean_all_is_sharpe": self.mean_all_is_sharpe,
            "mean_all_oos_sharpe": self.mean_all_oos_sharpe,
            "mean_oos_rank_correlation": self.mean_oos_rank_correlation,
            "performance_decay": self.performance_decay,
            "warnings": list(self.warnings),
        }
        if include_splits:
            payload["splits"] = [
                {
                    "split_index": split.split_index,
                    "in_sample_blocks": list(split.in_sample_blocks),
                    "out_of_sample_blocks": list(split.out_of_sample_blocks),
                    "in_sample_sharpe": list(split.in_sample_sharpe),
                    "out_of_sample_sharpe": list(split.out_of_sample_sharpe),
                    "in_sample_ranking": list(split.in_sample_ranking),
                    "out_of_sample_ranking": list(split.out_of_sample_ranking),
                    "selected_strategy": split.selected_strategy,
                    "selected_oos_rank": split.selected_oos_rank,
                    "selected_oos_percentile": split.selected_oos_percentile,
                    "logit": split.logit,
                    "overfit": split.overfit,
                }
                for split in self.splits
            ]
        return payload


def _validate_matrix(returns: Sequence[Sequence[float]]) -> tuple[int, int]:
    if not returns:
        raise ValueError("returns matrix is empty")
    n_observations = len(returns)
    n_strategies = len(returns[0])
    if n_strategies < 2:
        raise ValueError("PBO requires at least two strategies")
    if n_observations < 4:
        raise ValueError("PBO requires at least four observations")
    for row_index, row in enumerate(returns):
        if len(row) != n_strategies:
            raise ValueError(
                f"returns row {row_index} has {len(row)} values; expected {n_strategies}"
            )
        for value in row:
            if not isfinite(float(value)):
                raise ValueError("returns must be finite numbers")
    return n_observations, n_strategies


def make_partitions(n_observations: int, n_partitions: int) -> tuple[PBOPartition, ...]:
    """Split the timeline into contiguous, near-equal blocks."""
    if n_partitions < 2 or n_partitions % 2 != 0:
        raise ValueError("n_partitions must be an even number >= 2")
    if n_observations < n_partitions:
        raise ValueError("n_observations must be at least n_partitions")
    base, remainder = divmod(n_observations, n_partitions)
    partitions: list[PBOPartition] = []
    start = 0
    for index in range(n_partitions):
        size = base + (1 if index < remainder else 0)
        partitions.append(PBOPartition(index=index, start=start, end=start + size))
        start += size
    if any(part.size == 0 for part in partitions):
        raise ValueError("every partition must contain at least one observation")
    return tuple(partitions)


def cscv_block_combinations(
    n_partitions: int,
    *,
    max_splits: int | None = DEFAULT_MAX_SPLITS,
) -> Iterator[tuple[int, ...]]:
    """Yield every IS block combination; OOS is the complement."""
    if n_partitions < 2 or n_partitions % 2 != 0:
        raise ValueError("n_partitions must be an even number >= 2")
    total = comb(n_partitions, n_partitions // 2)
    if max_splits is not None and total > max_splits:
        raise ValueError(
            f"{total} CSCV splits exceed max_splits={max_splits}; "
            "reduce n_partitions or pass max_splits=None explicitly"
        )
    yield from combinations(range(n_partitions), n_partitions // 2)


def _sharpe(values: Sequence[float]) -> float:
    count = len(values)
    if count < 2:
        return 0.0
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    if variance <= 0.0:
        return 0.0
    return mean / sqrt(variance)


def _rank_descending(values: Sequence[float]) -> tuple[int, ...]:
    return tuple(sorted(range(len(values)), key=lambda index: (-values[index], index)))


def _average_ranks(values: Sequence[float]) -> list[float]:
    """Average ranks, ascending, with ties sharing the mean rank."""
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position + 1
        while end < len(order) and values[order[end]] == values[order[position]]:
            end += 1
        average = (position + 1 + end) / 2.0
        for offset in range(position, end):
            ranks[order[offset]] = average
        position = end
    return ranks


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_mean = sum(left_ranks) / len(left_ranks)
    right_mean = sum(right_ranks) / len(right_ranks)
    numerator = sum(
        (left_rank - left_mean) * (right_rank - right_mean)
        for left_rank, right_rank in zip(left_ranks, right_ranks, strict=True)
    )
    left_var = sum((rank - left_mean) ** 2 for rank in left_ranks)
    right_var = sum((rank - right_mean) ** 2 for rank in right_ranks)
    denominator = sqrt(left_var * right_var)
    return numerator / denominator if denominator > 0 else None


def _matrix_hash(returns: Sequence[Sequence[float]], strategy_ids: Sequence[str]) -> str:
    payload = {
        "strategy_ids": list(strategy_ids),
        "returns": [[float(value) for value in row] for row in returns],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _block_stats(
    returns: Sequence[Sequence[float]],
    partitions: Sequence[PBOPartition],
) -> tuple[tuple[tuple[int, float, float], ...], ...]:
    """Per-block (count, sum, sum-of-squares) for each strategy."""
    n_strategies = len(returns[0])
    stats: list[tuple[tuple[int, float, float], ...]] = []
    for part in partitions:
        block = returns[part.start : part.end]
        row: list[tuple[int, float, float]] = []
        for strategy in range(n_strategies):
            values = [float(block[index][strategy]) for index in range(len(block))]
            row.append((len(values), sum(values), sum(value * value for value in values)))
        stats.append(tuple(row))
    return tuple(stats)


def _combined_sharpe(
    block_stats: Sequence[tuple[tuple[int, float, float], ...]],
    blocks: Sequence[int],
    n_strategies: int,
) -> tuple[float, ...]:
    result: list[float] = []
    for strategy in range(n_strategies):
        count = sum(block_stats[block][strategy][0] for block in blocks)
        total = sum(block_stats[block][strategy][1] for block in blocks)
        total_sq = sum(block_stats[block][strategy][2] for block in blocks)
        if count < 2:
            result.append(0.0)
            continue
        mean = total / count
        variance = (total_sq - count * mean * mean) / (count - 1)
        if variance <= 0:
            result.append(0.0)
        else:
            result.append(mean / sqrt(variance))
    return tuple(result)


def calculate_pbo(
    returns: Sequence[Sequence[float]],
    *,
    experiment_id: str,
    strategy_ids: Sequence[str] | None = None,
    n_partitions: int = 16,
    max_splits: int | None = DEFAULT_MAX_SPLITS,
    store_splits: bool = True,
) -> PBOReport:
    """Run exact CSCV/PBO on a ``T x N`` strategy return matrix.

    ``returns[t][n]`` is the return of strategy ``n`` in period ``t``. All
    strategies must cover the same periods; the caller is responsible for
    aligning them and must not silently forward-fill missing observations.
    """
    if not experiment_id or not experiment_id.strip():
        raise ValueError("experiment_id is required for a PBO report")
    n_observations, n_strategies = _validate_matrix(returns)
    ids = tuple(strategy_ids) if strategy_ids is not None else tuple(
        f"strategy-{index}" for index in range(n_strategies)
    )
    if len(ids) != n_strategies:
        raise ValueError("strategy_ids must match the number of strategies")
    if len(set(ids)) != len(ids):
        raise ValueError("strategy_ids must be unique")
    if any(not strategy_id for strategy_id in ids):
        raise ValueError("strategy_ids cannot be empty")

    partitions = make_partitions(n_observations, n_partitions)
    block_stats = _block_stats(returns, partitions)
    combinations_list = list(cscv_block_combinations(n_partitions, max_splits=max_splits))

    splits: list[PBOSplit] = []
    selected_is: list[float] = []
    selected_oos: list[float] = []
    all_is: list[float] = []
    all_oos: list[float] = []
    correlations: list[float] = []
    overfit_count = 0
    warnings: list[str] = []
    zero_variance_blocks = 0

    for split_index, in_sample in enumerate(combinations_list):
        in_sample_set = set(in_sample)
        out_sample = tuple(block for block in range(n_partitions) if block not in in_sample_set)
        is_sharpe = _combined_sharpe(block_stats, in_sample, n_strategies)
        oos_sharpe = _combined_sharpe(block_stats, out_sample, n_strategies)
        if any(value == 0.0 for value in is_sharpe):
            zero_variance_blocks += 1

        is_ranking = _rank_descending(is_sharpe)
        oos_ranking = _rank_descending(oos_sharpe)
        selected = is_ranking[0]
        selected_rank = oos_ranking.index(selected) + 1  # 1 = best
        percentile = (n_strategies - selected_rank) / (n_strategies - 1)
        # Standard CSCV relative rank: best -> N/(N+1), worst -> 1/(N+1).
        relative = (n_strategies - selected_rank + 1) / (n_strategies + 1)
        logit = log(relative / (1.0 - relative))
        overfit = logit <= 0.0
        overfit_count += int(overfit)

        selected_is.append(is_sharpe[selected])
        selected_oos.append(oos_sharpe[selected])
        all_is.extend(is_sharpe)
        all_oos.extend(oos_sharpe)
        correlation = _spearman(is_sharpe, oos_sharpe)
        if correlation is not None:
            correlations.append(correlation)

        if store_splits:
            splits.append(
                PBOSplit(
                    split_index=split_index,
                    in_sample_blocks=tuple(in_sample),
                    out_of_sample_blocks=out_sample,
                    in_sample_sharpe=is_sharpe,
                    out_of_sample_sharpe=oos_sharpe,
                    in_sample_ranking=is_ranking,
                    out_of_sample_ranking=oos_ranking,
                    selected_strategy=selected,
                    selected_oos_rank=selected_rank,
                    selected_oos_percentile=percentile,
                    logit=logit,
                    overfit=overfit,
                )
            )

    if zero_variance_blocks:
        warnings.append(
            f"{zero_variance_blocks} split(s) contained a zero-variance Sharpe estimate"
        )

    return PBOReport(
        experiment_id=experiment_id,
        pbo=overfit_count / len(combinations_list),
        n_splits=len(combinations_list),
        n_strategies=n_strategies,
        n_observations=n_observations,
        n_partitions=n_partitions,
        calculation_version=PBO_VERSION,
        returns_hash=_matrix_hash(returns, ids),
        strategy_ids=ids,
        partitions=partitions,
        splits=tuple(splits),
        mean_selected_is_sharpe=sum(selected_is) / len(selected_is),
        mean_selected_oos_sharpe=sum(selected_oos) / len(selected_oos),
        mean_all_is_sharpe=sum(all_is) / len(all_is),
        mean_all_oos_sharpe=sum(all_oos) / len(all_oos),
        mean_oos_rank_correlation=(
            sum(correlations) / len(correlations) if correlations else None
        ),
        warnings=tuple(warnings),
    )


__all__ = [
    "DEFAULT_MAX_SPLITS",
    "PBO_VERSION",
    "PBOPartition",
    "PBOReport",
    "PBOSplit",
    "calculate_pbo",
    "cscv_block_combinations",
    "make_partitions",
]
