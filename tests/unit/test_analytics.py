"""Phase 6 execution-analytics tests (spec §11 / §15.6)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from axel.core.types import OrderSide, OrderType
from axel.execution.analytics import (
    BenchmarkInput,
    ExecutionMetrics,
    FillInput,
    OrderAnalyticsInput,
    ReversionPoint,
    compute_execution_metrics,
    summarize_cohort,
)
from axel.execution.reason_codes import MetricStatus

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)


def _input(**overrides) -> OrderAnalyticsInput:
    base = {
        "client_order_id": "ord-1",
        "symbol": "SPY",
        "side": OrderSide.BUY,
        "requested_qty": Decimal(100),
        "order_type": OrderType.LIMIT,
        "decision_price": Decimal(100),
        "decision_time": T0,
        "submit_time": T0,
        "ack_time": T0 + timedelta(seconds=1),
        "first_fill_time": T0 + timedelta(seconds=2),
        "completion_time": T0 + timedelta(seconds=3),
        "fills": (
            FillInput(qty=Decimal(40), price=Decimal(101), fee=Decimal("0.4"), event_time=T0),
            FillInput(qty=Decimal(60), price=Decimal(102), fee=Decimal("0.6"), event_time=T0),
        ),
    }
    base.update(overrides)
    return OrderAnalyticsInput(**base)


def test_buy_sign_convention_positive_is_worse():
    metrics = compute_execution_metrics(_input())
    # avg = (40*101 + 60*102)/100 = 101.6 ; IS = +1 * (101.6 - 100) = +1.6
    assert metrics.avg_fill_price == Decimal("101.6")
    assert metrics.implementation_shortfall_per_share == Decimal("1.6")
    # Dollar IS = 40*1 + 60*2 = 160
    assert metrics.implementation_shortfall_filled == Decimal(160)
    assert metrics.metric_status("implementation_shortfall_per_share") is MetricStatus.COMPUTABLE


def test_sell_sign_convention_is_mirrored():
    metrics = compute_execution_metrics(
        _input(
            side=OrderSide.SELL,
            fills=(FillInput(qty=Decimal(100), price=Decimal(98)),),
        )
    )
    # Selling below arrival is adverse: IS = -1 * (98 - 100) = +2
    assert metrics.implementation_shortfall_per_share == Decimal(2)


def test_partial_fills_weighted_by_quantity_and_fill_ratio():
    metrics = compute_execution_metrics(
        _input(fills=(FillInput(qty=Decimal(25), price=Decimal(100)),))
    )
    assert metrics.filled_qty == Decimal(25)
    assert metrics.fill_ratio == Decimal("0.25")
    assert metrics.avg_fill_price == Decimal(100)


def test_fees_counted_exactly_once():
    metrics = compute_execution_metrics(_input())
    assert metrics.fees_total == Decimal("1.0")
    assert metrics.net_implementation_shortfall_filled == Decimal("161.0")
    # Gross IS excludes fees.
    assert metrics.implementation_shortfall_filled == Decimal(160)


def test_zero_fills_is_not_computable_not_fabricated():
    metrics = compute_execution_metrics(_input(fills=()))
    assert metrics.filled_qty == Decimal(0)
    assert metrics.fill_ratio == Decimal(0)
    assert metrics.avg_fill_price is None
    assert metrics.implementation_shortfall_per_share is None
    assert metrics.metric_status("implementation_shortfall_per_share") is MetricStatus.NOT_COMPUTABLE


def test_no_decision_price_means_no_shortfall():
    metrics = compute_execution_metrics(_input(decision_price=None))
    assert metrics.implementation_shortfall_per_share is None
    assert metrics.metric_status("implementation_shortfall_per_share") is MetricStatus.NOT_COMPUTABLE


def test_vwap_benchmark_recorded_and_slippage_computed():
    metrics = compute_execution_metrics(
        _input(
            benchmark=BenchmarkInput(
                vwap=Decimal(101),
                window_start=T0,
                window_end=T0 + timedelta(minutes=5),
                source="consolidated_tape",
                print_count=1234,
            )
        )
    )
    # avg 101.6 vs vwap 101 -> +0.6 for a buy
    assert metrics.vwap_slippage_per_share == Decimal("0.6")
    assert metrics.vwap_benchmark == Decimal(101)
    assert metrics.vwap_benchmark_source == "consolidated_tape"
    assert metrics.vwap_window_start == T0
    assert metrics.vwap_window_end == T0 + timedelta(minutes=5)
    assert metrics.vwap_print_count == 1234
    assert metrics.metric_status("vwap_slippage_per_share") is MetricStatus.COMPUTABLE


def test_missing_vwap_source_is_partial_data():
    metrics = compute_execution_metrics(
        _input(
            benchmark=BenchmarkInput(
                vwap=Decimal(101),
                window_start=T0,
                window_end=T0 + timedelta(minutes=5),
            )
        )
    )
    assert metrics.vwap_slippage_per_share == Decimal("0.6")
    assert metrics.metric_status("vwap_slippage_per_share") is MetricStatus.PARTIAL_DATA


def test_no_benchmark_means_not_computable():
    metrics = compute_execution_metrics(_input())
    assert metrics.vwap_slippage_per_share is None
    assert metrics.metric_status("vwap_slippage_per_share") is MetricStatus.NOT_COMPUTABLE


def test_ineligible_benchmark_is_not_computable():
    metrics = compute_execution_metrics(
        _input(benchmark=BenchmarkInput(vwap=Decimal(101), eligible=False))
    )
    assert metrics.vwap_slippage_per_share is None
    assert metrics.metric_status("vwap_slippage_per_share") is MetricStatus.NOT_COMPUTABLE


def test_post_completion_reversion_signed_by_side():
    metrics = compute_execution_metrics(
        _input(reversion_points=(ReversionPoint(horizon_seconds=60, reference_price=Decimal(103)),))
    )
    # Buy: reference above fill is adverse -> +1.4
    assert metrics.reversion_by_horizon[60] == Decimal("1.4")


def test_timestamps_measured_from_submit():
    metrics = compute_execution_metrics(_input())
    assert metrics.time_to_ack_seconds == 1.0
    assert metrics.time_to_first_fill_seconds == 2.0
    assert metrics.time_to_completion_seconds == 3.0


def test_arrival_spread_from_quotes():
    metrics = compute_execution_metrics(
        _input(arrival_best_bid=Decimal("99.99"), arrival_best_ask=Decimal("100.01"))
    )
    assert metrics.arrival_spread == Decimal("0.02")


def test_metrics_are_reproducible_from_stored_raw_inputs():
    inp = _input()
    first = compute_execution_metrics(inp)
    second = compute_execution_metrics(inp)
    assert first.to_row() == second.to_row()
    assert first.calculation_version == "exec-metrics-1"


def test_every_metric_has_a_versioned_definition():
    for name in ExecutionMetrics.definitions():
        definition = ExecutionMetrics.definitions()[name]
        assert definition.unit
        assert definition.side_convention
        assert definition.description


def test_cohort_summary_reports_distribution_and_missing_rate():
    a = compute_execution_metrics(_input())
    b = compute_execution_metrics(
        _input(
            client_order_id="ord-2",
            decision_price=None,  # not computable -> contributes to missing rate
        )
    )
    assert a.metric_status("implementation_shortfall_per_share") is MetricStatus.COMPUTABLE
    assert b.metric_status("implementation_shortfall_per_share") is MetricStatus.NOT_COMPUTABLE

    summary = summarize_cohort([a, b], min_sample=5)
    assert summary.order_count == 2
    assert summary.is_per_share_median == Decimal("1.6")
    assert summary.missing_data_rate == 0.5
    assert summary.insufficient_evidence is True


def test_cohort_flags_sufficient_evidence():
    metrics = [compute_execution_metrics(_input(client_order_id=f"ord-{i}")) for i in range(3)]
    summary = summarize_cohort(metrics, min_sample=3)
    assert summary.insufficient_evidence is False
    assert summary.total_implementation_shortfall_filled == Decimal(480)
