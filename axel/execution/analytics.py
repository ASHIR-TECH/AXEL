"""
Execution analytics and cost metrics (Phase 6, spec §11).

Raw capture happens at decision/arrival time, submit time and fill time; metrics
are then **derived** by a tested, versioned service so nothing is invented.

Conventions (spec §11.3), fixed and documented in :data:`METRIC_DEFINITIONS`:

* side sign ``s = +1`` for a buy and ``s = -1`` for a sell;
* signed implementation shortfall ``IS = s * (P_fill - P_arrival)`` --- positive
  means worse execution;
* signed VWAP slippage ``C = s * (P_fill - P_vwap)``;
* ``implementation_shortfall_filled`` excludes fees; ``fees_total`` is reported
  separately and ``net_implementation_shortfall_filled`` adds them exactly once;
* a metric that cannot be computed from available data is reported as
  ``NOT_COMPUTABLE`` (or ``PARTIAL_DATA``) rather than fabricated.

This module has zero LLM imports and no broker dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from axel.core.money import to_decimal
from axel.core.types import OrderSide, OrderType
from axel.execution.reason_codes import MetricStatus

CALCULATION_VERSION = "exec-metrics-1"


@dataclass(frozen=True)
class MetricDefinition:
    """Versioned definition of a single metric (spec §11.3)."""

    name: str
    unit: str
    side_convention: str
    fees_included: bool
    description: str
    reference: str | None = None


METRIC_DEFINITIONS: dict[str, MetricDefinition] = {
    "fill_ratio": MetricDefinition(
        name="fill_ratio",
        unit="ratio",
        side_convention="n/a",
        fees_included=False,
        description="filled quantity / requested quantity",
    ),
    "avg_fill_price": MetricDefinition(
        name="avg_fill_price",
        unit="USD/share",
        side_convention="n/a",
        fees_included=False,
        description="quantity-weighted average fill price",
    ),
    "implementation_shortfall_per_share": MetricDefinition(
        name="implementation_shortfall_per_share",
        unit="USD/share",
        side_convention="s=+1 buy, s=-1 sell; positive is worse",
        fees_included=False,
        description="s * (avg_fill_price - arrival_price)",
        reference="arrival/decision price",
    ),
    "implementation_shortfall_filled": MetricDefinition(
        name="implementation_shortfall_filled",
        unit="USD",
        side_convention="s=+1 buy, s=-1 sell; positive is worse",
        fees_included=False,
        description="s * sum_i q_i * (p_i - arrival_price) over filled quantity",
        reference="arrival/decision price",
    ),
    "net_implementation_shortfall_filled": MetricDefinition(
        name="net_implementation_shortfall_filled",
        unit="USD",
        side_convention="s=+1 buy, s=-1 sell; positive is worse",
        fees_included=True,
        description="implementation_shortfall_filled + fees_total (fees counted once)",
        reference="arrival/decision price",
    ),
    "vwap_slippage_per_share": MetricDefinition(
        name="vwap_slippage_per_share",
        unit="USD/share",
        side_convention="s=+1 buy, s=-1 sell; positive is worse",
        fees_included=False,
        description="s * (avg_fill_price - vwap_benchmark)",
        reference="market VWAP over the recorded window",
    ),
    "fees_total": MetricDefinition(
        name="fees_total",
        unit="USD",
        side_convention="n/a",
        fees_included=True,
        description="sum of commissions/fees across fills",
    ),
    "post_completion_reversion": MetricDefinition(
        name="post_completion_reversion",
        unit="USD/share",
        side_convention="s=+1 buy, s=-1 sell; positive is adverse",
        fees_included=False,
        description="s * (reference_price - avg_fill_price) at a fixed horizon",
        reference="post-completion reference price",
    ),
}


@dataclass(frozen=True)
class FillInput:
    """One raw fill as captured from the broker."""

    qty: Decimal
    price: Decimal
    fee: Decimal = Decimal(0)
    event_time: datetime | None = None


@dataclass(frozen=True)
class BenchmarkInput:
    """A VWAP benchmark and its coverage metadata (spec §11.1/§11.2)."""

    vwap: Decimal | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    source: str | None = None
    print_count: int | None = None
    eligible: bool = True


@dataclass(frozen=True)
class ReversionPoint:
    horizon_seconds: int
    reference_price: Decimal


@dataclass(frozen=True)
class OrderAnalyticsInput:
    """All raw fields required to compute one order's execution metrics."""

    client_order_id: str
    symbol: str
    side: OrderSide
    requested_qty: Decimal
    order_type: OrderType = OrderType.LIMIT
    strategy_id: str | None = None
    strategy_version: str | None = None
    proposal_id: str | None = None
    approval_id: str | None = None
    risk_decision_id: str | None = None
    decision_price: Decimal | None = None
    decision_time: datetime | None = None
    submit_time: datetime | None = None
    ack_time: datetime | None = None
    first_fill_time: datetime | None = None
    completion_time: datetime | None = None
    fills: tuple[FillInput, ...] = ()
    benchmark: BenchmarkInput | None = None
    reversion_points: tuple[ReversionPoint, ...] = ()
    cancel_count: int = 0
    reject_count: int = 0
    retry_count: int = 0
    ambiguous_count: int = 0
    recon_delay_seconds: float | None = None
    mismatch_count: int = 0
    arrival_best_bid: Decimal | None = None
    arrival_best_ask: Decimal | None = None


@dataclass(frozen=True)
class ExecutionMetrics:
    """Computed, self-describing execution metrics for one order."""

    client_order_id: str
    symbol: str
    side: OrderSide
    requested_qty: Decimal
    filled_qty: Decimal
    fill_ratio: Decimal
    avg_fill_price: Decimal | None
    filled_notional: Decimal
    fees_total: Decimal
    implementation_shortfall_per_share: Decimal | None
    implementation_shortfall_filled: Decimal | None
    net_implementation_shortfall_filled: Decimal | None
    vwap_benchmark: Decimal | None
    vwap_slippage_per_share: Decimal | None
    vwap_benchmark_source: str | None
    vwap_window_start: datetime | None
    vwap_window_end: datetime | None
    vwap_print_count: int | None
    reversion_by_horizon: dict[int, Decimal] = field(default_factory=dict)
    arrival_spread: Decimal | None = None
    time_to_ack_seconds: float | None = None
    time_to_first_fill_seconds: float | None = None
    time_to_completion_seconds: float | None = None
    cancel_count: int = 0
    reject_count: int = 0
    retry_count: int = 0
    ambiguous_count: int = 0
    recon_delay_seconds: float | None = None
    mismatch_count: int = 0
    status: dict[str, MetricStatus] = field(default_factory=dict)
    calculation_version: str = CALCULATION_VERSION

    def metric_status(self, name: str) -> MetricStatus:
        return self.status.get(name, MetricStatus.NOT_COMPUTABLE)

    @staticmethod
    def definitions() -> dict[str, MetricDefinition]:
        return dict(METRIC_DEFINITIONS)

    @property
    def has_fills(self) -> bool:
        return self.filled_qty > 0

    def to_row(self) -> dict:
        """Flat, JSON-friendly row for persistence/export (raw + derived)."""
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "requested_qty": str(self.requested_qty),
            "filled_qty": str(self.filled_qty),
            "fill_ratio": str(self.fill_ratio),
            "avg_fill_price": _s(self.avg_fill_price),
            "filled_notional": str(self.filled_notional),
            "fees_total": str(self.fees_total),
            "implementation_shortfall_per_share": _s(self.implementation_shortfall_per_share),
            "implementation_shortfall_filled": _s(self.implementation_shortfall_filled),
            "net_implementation_shortfall_filled": _s(self.net_implementation_shortfall_filled),
            "vwap_benchmark": _s(self.vwap_benchmark),
            "vwap_slippage_per_share": _s(self.vwap_slippage_per_share),
            "vwap_benchmark_source": self.vwap_benchmark_source,
            "vwap_window_start": _dt(self.vwap_window_start),
            "vwap_window_end": _dt(self.vwap_window_end),
            "vwap_print_count": self.vwap_print_count,
            "arrival_spread": _s(self.arrival_spread),
            "cancel_count": self.cancel_count,
            "reject_count": self.reject_count,
            "retry_count": self.retry_count,
            "ambiguous_count": self.ambiguous_count,
            "recon_delay_seconds": self.recon_delay_seconds,
            "mismatch_count": self.mismatch_count,
            "status": {k: v.value for k, v in self.status.items()},
            "calculation_version": self.calculation_version,
        }


def compute_execution_metrics(inp: OrderAnalyticsInput) -> ExecutionMetrics:
    """Pure function: derive all metrics from captured raw inputs."""
    side_sign = Decimal(1) if inp.side is OrderSide.BUY else Decimal(-1)
    status: dict[str, MetricStatus] = {}

    requested = to_decimal(inp.requested_qty)
    filled_qty = sum((to_decimal(f.qty) for f in inp.fills), Decimal(0))
    filled_notional = sum((to_decimal(f.qty) * to_decimal(f.price) for f in inp.fills), Decimal(0))
    fees_total = sum((to_decimal(f.fee) for f in inp.fills), Decimal(0))

    fill_ratio = (filled_qty / requested) if requested > 0 else Decimal(0)
    status["fill_ratio"] = MetricStatus.COMPUTABLE if requested > 0 else MetricStatus.NOT_COMPUTABLE

    if filled_qty > 0:
        avg_fill_price: Decimal | None = filled_notional / filled_qty
        status["avg_fill_price"] = MetricStatus.COMPUTABLE
    else:
        avg_fill_price = None
        status["avg_fill_price"] = MetricStatus.NOT_COMPUTABLE

    # Implementation shortfall against the arrival/decision price.
    arrival = to_decimal(inp.decision_price) if inp.decision_price is not None else None
    if avg_fill_price is None:
        is_per_share = None
        is_filled = None
        net_is_filled = None
        status["implementation_shortfall_per_share"] = MetricStatus.NOT_COMPUTABLE
        status["implementation_shortfall_filled"] = MetricStatus.NOT_COMPUTABLE
        status["net_implementation_shortfall_filled"] = MetricStatus.NOT_COMPUTABLE
    elif arrival is None:
        is_per_share = is_filled = net_is_filled = None
        status["implementation_shortfall_per_share"] = MetricStatus.NOT_COMPUTABLE
        status["implementation_shortfall_filled"] = MetricStatus.NOT_COMPUTABLE
        status["net_implementation_shortfall_filled"] = MetricStatus.NOT_COMPUTABLE
    else:
        is_per_share = side_sign * (avg_fill_price - arrival)
        is_filled = side_sign * sum(
            (to_decimal(f.qty) * (to_decimal(f.price) - arrival) for f in inp.fills),
            Decimal(0),
        )
        net_is_filled = is_filled + fees_total  # fees included exactly once
        status["implementation_shortfall_per_share"] = MetricStatus.COMPUTABLE
        status["implementation_shortfall_filled"] = MetricStatus.COMPUTABLE
        status["net_implementation_shortfall_filled"] = MetricStatus.COMPUTABLE

    # VWAP benchmark.
    vwap_benchmark = None
    vwap_slippage = None
    vwap_source = None
    vwap_start = None
    vwap_end = None
    vwap_prints = None
    if inp.benchmark is None:
        status["vwap_slippage_per_share"] = MetricStatus.NOT_COMPUTABLE
    else:
        bench = inp.benchmark
        vwap_source = bench.source
        vwap_start = bench.window_start
        vwap_end = bench.window_end
        vwap_prints = bench.print_count
        if not bench.eligible or bench.vwap is None or bench.window_start is None or bench.window_end is None:
            status["vwap_slippage_per_share"] = MetricStatus.NOT_COMPUTABLE
        else:
            vwap_benchmark = to_decimal(bench.vwap)
            if avg_fill_price is None:
                vwap_slippage = None
                status["vwap_slippage_per_share"] = MetricStatus.NOT_COMPUTABLE
            else:
                vwap_slippage = side_sign * (avg_fill_price - vwap_benchmark)
                status["vwap_slippage_per_share"] = (
                    MetricStatus.COMPUTABLE
                    if bench.source is not None and bench.print_count is not None
                    else MetricStatus.PARTIAL_DATA
                )

    # Post-completion reversion (diagnostic, versioned horizons).
    reversion: dict[int, Decimal] = {}
    if inp.reversion_points and avg_fill_price is not None:
        for point in inp.reversion_points:
            reversion[point.horizon_seconds] = side_sign * (
                to_decimal(point.reference_price) - avg_fill_price
            )
    status["post_completion_reversion"] = (
        MetricStatus.COMPUTABLE if reversion else MetricStatus.NOT_COMPUTABLE
    )

    # Arrival spread, when quote data exists.
    if inp.arrival_best_bid is not None and inp.arrival_best_ask is not None:
        arrival_spread = to_decimal(inp.arrival_best_ask) - to_decimal(inp.arrival_best_bid)
        status["arrival_spread"] = MetricStatus.COMPUTABLE
    else:
        arrival_spread = None
        status["arrival_spread"] = MetricStatus.NOT_COMPUTABLE

    status["fees_total"] = MetricStatus.COMPUTABLE

    return ExecutionMetrics(
        client_order_id=inp.client_order_id,
        symbol=inp.symbol,
        side=inp.side,
        requested_qty=requested,
        filled_qty=filled_qty,
        fill_ratio=fill_ratio,
        avg_fill_price=avg_fill_price,
        filled_notional=filled_notional,
        fees_total=fees_total,
        implementation_shortfall_per_share=is_per_share,
        implementation_shortfall_filled=is_filled,
        net_implementation_shortfall_filled=net_is_filled,
        vwap_benchmark=vwap_benchmark,
        vwap_slippage_per_share=vwap_slippage,
        vwap_benchmark_source=vwap_source,
        vwap_window_start=vwap_start,
        vwap_window_end=vwap_end,
        vwap_print_count=vwap_prints,
        reversion_by_horizon=reversion,
        arrival_spread=arrival_spread,
        time_to_ack_seconds=_delta_seconds(inp.submit_time, inp.ack_time),
        time_to_first_fill_seconds=_delta_seconds(inp.submit_time, inp.first_fill_time),
        time_to_completion_seconds=_delta_seconds(inp.submit_time, inp.completion_time),
        cancel_count=inp.cancel_count,
        reject_count=inp.reject_count,
        retry_count=inp.retry_count,
        ambiguous_count=inp.ambiguous_count,
        recon_delay_seconds=inp.recon_delay_seconds,
        mismatch_count=inp.mismatch_count,
        status=status,
    )


@dataclass(frozen=True)
class CohortSummary:
    """Aggregate metrics across orders, with sample size and missing-data rate."""

    order_count: int
    filled_notional: Decimal
    fees_total: Decimal
    fill_ratio_median: Decimal | None
    fill_ratio_p90: Decimal | None
    is_per_share_median: Decimal | None
    is_per_share_p90: Decimal | None
    total_implementation_shortfall_filled: Decimal
    missing_data_rate: float
    insufficient_evidence: bool


def summarize_cohort(
    metrics: list[ExecutionMetrics],
    *,
    min_sample: int | None = None,
) -> CohortSummary:
    """
    Aggregate a cohort without hiding distribution shape behind a mean.

    ``min_sample`` is supplied by the caller (research policy), never invented
    here; when the sample is smaller the result is flagged
    ``insufficient_evidence``.
    """
    order_count = len(metrics)
    filled_notional = sum((m.filled_notional for m in metrics), Decimal(0))
    fees_total = sum((m.fees_total for m in metrics), Decimal(0))
    total_is = sum(
        (m.implementation_shortfall_filled or Decimal(0) for m in metrics), Decimal(0)
    )

    fill_ratios = [m.fill_ratio for m in metrics if m.metric_status("fill_ratio") is MetricStatus.COMPUTABLE]
    is_values = [
        m.implementation_shortfall_per_share
        for m in metrics
        if m.metric_status("implementation_shortfall_per_share") is MetricStatus.COMPUTABLE
        and m.implementation_shortfall_per_share is not None
    ]
    is_computable = sum(
        1
        for m in metrics
        if m.metric_status("implementation_shortfall_per_share") is MetricStatus.COMPUTABLE
    )
    missing_rate = 0.0 if order_count == 0 else (order_count - is_computable) / order_count

    return CohortSummary(
        order_count=order_count,
        filled_notional=filled_notional,
        fees_total=fees_total,
        fill_ratio_median=_percentile(fill_ratios, Decimal("0.5")),
        fill_ratio_p90=_percentile(fill_ratios, Decimal("0.9")),
        is_per_share_median=_percentile(is_values, Decimal("0.5")),
        is_per_share_p90=_percentile(is_values, Decimal("0.9")),
        total_implementation_shortfall_filled=total_is,
        missing_data_rate=missing_rate,
        insufficient_evidence=min_sample is not None and order_count < min_sample,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _delta_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds()


def _percentile(values: list[Decimal], q: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    # Linear interpolation between closest ranks.
    pos = q * Decimal(len(ordered) - 1)
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    frac = pos - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * frac


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _dt(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
