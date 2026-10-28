"""Shared deterministic fixtures for Phase 11 portfolio tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from axel.core.types import Section
from axel.portfolio.budget import propose_risk_budgets
from axel.portfolio.constraints import evaluate_allocation_constraints
from axel.portfolio.cost import estimate_rebalance_cost
from axel.portfolio.normalize import build_exposure_snapshot
from axel.portfolio.optimizer import generate_allocation
from axel.portfolio.proposal import (
    build_allocation_proposal,
    build_rebalance_proposal,
)
from axel.portfolio.registry import InstrumentRegistry
from axel.portfolio.risk import aggregate_risk_report
from axel.portfolio.state import (
    DEFAULT_FRESHNESS,
    build_portfolio_snapshot,
)
from axel.portfolio.stress import run_stress_suite
from axel.portfolio.types import (
    AccountCapital,
    AllocationProposal,
    AssetClass,
    CapitalSummary,
    CostSummary,
    DataQuality,
    ExposureSnapshot,
    FxRate,
    InstrumentSpec,
    InstrumentType,
    OptimizationConfig,
    OptimizationMethod,
    OptionRiskInput,
    PortfolioSnapshot,
    PositionRecord,
    PredictionOutcome,
    PredictionRiskInput,
    RebalanceProposal,
    ReconciliationStatus,
    RiskBudgetPolicy,
    SectionReadiness,
    SectionStatus,
    SourceRef,
)
from axel.portfolio.validator import (
    ExecutionMode,
    PortfolioValidationPolicy,
    ValidationContext,
)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
FUTURE = NOW + timedelta(days=180)


def stock_position(
    instrument_id: str = "stock_a",
    *,
    quantity: float = 100.0,
    price: float = 100.0,
    section: Section = Section.STOCKS,
    venue: str = "test",
    currency: str = "USD",
    now: datetime = NOW,
) -> PositionRecord:
    return PositionRecord(
        position_id=f"pos_{instrument_id}",
        instrument_id=instrument_id,
        section=section,
        venue=venue,
        quantity=quantity,
        multiplier=1.0,
        price=price,
        currency=currency,
        market_value=quantity * price,
        unrealized_pnl=0.0,
        as_of=now,
        source="test_source",
        valuation_method="market_value",
        quality=DataQuality.VALID,
        reconciliation_status=ReconciliationStatus.RECONCILED,
    )


def live_status(section: Section, now: datetime = NOW) -> SectionStatus:
    return SectionStatus(
        section=section,
        readiness=SectionReadiness.LIVE_ELIGIBLE,
        data_quality=DataQuality.VALID,
        reconciliation_status=ReconciliationStatus.RECONCILED,
        last_update=now,
        source="test_status",
    )


def all_live_statuses(now: datetime = NOW) -> tuple[SectionStatus, ...]:
    return tuple(live_status(section, now) for section in Section)


def make_snapshot(
    *,
    now: datetime = NOW,
    equity: float = 100_000.0,
    positions: tuple[PositionRecord, ...] | None = None,
    statuses: tuple[SectionStatus, ...] | None = None,
    snapshot_id: str = "snap_test",
    fx_rates: tuple[FxRate, ...] = (),
) -> PortfolioSnapshot:
    if positions is None:
        positions = (
            stock_position("stock_a", quantity=100.0, price=100.0, now=now),
            stock_position("stock_b", quantity=50.0, price=200.0, now=now),
        )
    if statuses is None:
        statuses = all_live_statuses(now)
    account = AccountCapital(
        account_id="acct_test",
        venue="test",
        currency="USD",
        cash_balance=equity,
        equity_value=equity,
        account_equity=equity,
        reserved_order_capacity=0.0,
        margin_requirement=0.0,
        as_of=now,
        source="test_account",
        quality=DataQuality.VALID,
        available_to_trade=equity,
        risk_budget_remaining=equity,
        liquidation_value_estimate=equity,
    )
    return build_portfolio_snapshot(
        accounts=(account,),
        positions=positions,
        open_orders=(),
        commitments=(),
        section_status=statuses,
        fx_rates=fx_rates,
        now=now,
        base_currency="USD",
        freshness=DEFAULT_FRESHNESS,
        snapshot_version=1,
        snapshot_id=snapshot_id,
    )


def make_registry(*, include_option: bool = False) -> InstrumentRegistry:
    registry = InstrumentRegistry(version="test-registry-1")
    registry.register_many(
        (
            InstrumentSpec(
                instrument_id="stock_a",
                symbol="A",
                section=Section.STOCKS,
                asset_class=AssetClass.EQUITY,
                instrument_type=InstrumentType.STOCK,
                venue="test",
                currency="USD",
                settlement_currency="USD",
            ),
            InstrumentSpec(
                instrument_id="stock_b",
                symbol="B",
                section=Section.STOCKS,
                asset_class=AssetClass.EQUITY,
                instrument_type=InstrumentType.STOCK,
                venue="test",
                currency="USD",
                settlement_currency="USD",
            ),
        )
    )
    if include_option:
        registry.register(
            InstrumentSpec(
                instrument_id="call_a",
                symbol="CALL_A",
                section=Section.OPTIONS,
                asset_class=AssetClass.OPTION,
                instrument_type=InstrumentType.CALL,
                venue="test",
                currency="USD",
                settlement_currency="USD",
                underlying_id="stock_a",
                expiry=FUTURE,
                exercise_style="european",
                multiplier=100.0,
            )
        )
    return registry


def make_exposure(
    snapshot: PortfolioSnapshot,
    registry: InstrumentRegistry | None = None,
    *,
    now: datetime = NOW,
) -> ExposureSnapshot:
    return build_exposure_snapshot(
        snapshot,
        registry or make_registry(),
        now=now,
        freshness=DEFAULT_FRESHNESS,
    )


def make_policy(**overrides: object) -> RiskBudgetPolicy:
    values: dict[str, object] = {
        "policy_version": "test-policy-1",
        "global_max_gross_exposure": 100_000.0,
        "global_max_net_exposure": 100_000.0,
        "global_max_var": None,
        "global_max_expected_shortfall": None,
        "global_max_stress_loss": 50_000.0,
        "global_max_margin": None,
        "global_max_turnover": 1.0,
        "global_max_concentration_hhi": 1.0,
        "max_leverage": 1.0,
        "max_single_instrument_weight": 1.0,
        "max_illiquid_weight": 1.0,
        "min_cash_reserve_pct": 0.0,
        "incomplete_data_haircut": 0.5,
        "section_caps": (),
    }
    values.update(overrides)
    return RiskBudgetPolicy(**values)  # type: ignore[arg-type]


def make_budget(
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    policy: RiskBudgetPolicy,
    *,
    now: datetime = NOW,
):
    return propose_risk_budgets(snapshot, exposure, policy, now=now)


def make_covariance() -> tuple[tuple[float, float], tuple[float, float]]:
    return ((0.04, 0.01), (0.01, 0.09))


def make_allocation(
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    budget,
    policy: RiskBudgetPolicy,
    *,
    instrument_ids: tuple[str, str] = ("stock_a", "stock_b"),
    now: datetime = NOW,
):
    section_map = {instrument_id: Section.STOCKS for instrument_id in instrument_ids}
    equity = snapshot.capital_summary.equity_value or 0.0
    current = tuple(
        (exposure.section(Section.STOCKS).net_market_value / 2.0 / equity)
        for _ in instrument_ids
    )
    return generate_allocation(
        snapshot=snapshot,
        exposure=exposure,
        budget=budget,
        config=OptimizationConfig(
            method=OptimizationMethod.EQUAL_WEIGHT,
            upper_bound=1.0,
            require_comparable_assets=True,
        ),
        covariance=make_covariance(),
        instrument_ids=instrument_ids,
        section_by_instrument=section_map,
        expected_returns=(0.10, 0.12),
        current_weights=current,
        policy=policy,
        stress_passed=True,
        eligible_instruments=instrument_ids,
        available_cash=snapshot.capital_summary.available_to_trade,
        now=now,
    )


def make_risk_report(
    snapshot: PortfolioSnapshot,
    weights: tuple[float, ...] = (0.5, 0.5),
    *,
    instrument_ids: tuple[str, str] = ("stock_a", "stock_b"),
):
    equity = snapshot.capital_summary.equity_value or 0.0
    returns = tuple(0.002 * ((index % 11) - 5) for index in range(60))
    values = tuple(100_000.0 * (1.0 + 0.001 * index) for index in range(60))
    return aggregate_risk_report(
        instrument_ids=instrument_ids,
        weights=weights,
        covariance=make_covariance(),
        portfolio_returns=returns,
        portfolio_values=values,
        section_gross_exposure={"stocks": sum(abs(value) for value in weights) * equity},
        gross_exposure=sum(abs(value) for value in weights) * equity,
        net_exposure=sum(weights) * equity,
        max_loss=None,
        margin_requirement=0.0,
        liquidity_horizon_days=1.0,
    )


def make_stress_report(
    snapshot: PortfolioSnapshot,
    exposure: ExposureSnapshot,
    policy: RiskBudgetPolicy,
    *,
    now: datetime = NOW,
):
    return run_stress_suite(snapshot, exposure, policy=policy, now=now)


def make_cost_summary(*, now: datetime = NOW) -> CostSummary:
    return estimate_rebalance_cost((), now=now)


def make_proposal_bundle(
    *,
    now: datetime = NOW,
    snapshot: PortfolioSnapshot | None = None,
    exposure: ExposureSnapshot | None = None,
    policy: RiskBudgetPolicy | None = None,
) -> tuple[AllocationProposal, RebalanceProposal, PortfolioSnapshot, ExposureSnapshot, RiskBudgetPolicy]:
    snapshot = snapshot or make_snapshot(now=now)
    exposure = exposure or make_exposure(snapshot, now=now)
    policy = policy or make_policy()
    budget = make_budget(snapshot, exposure, policy, now=now)
    allocation = make_allocation(snapshot, exposure, budget, policy, now=now)
    risk_report = make_risk_report(snapshot)
    stress_report = make_stress_report(snapshot, exposure, policy, now=now)
    section_map = {"stock_a": Section.STOCKS, "stock_b": Section.STOCKS}
    proposal = build_allocation_proposal(
        snapshot=snapshot,
        exposure=exposure,
        budget=budget,
        allocation=allocation,
        policy=policy,
        cost_summary=make_cost_summary(now=now),
        risk_summary=risk_report.to_summary(),
        section_by_instrument=section_map,
        stress_report=stress_report,
        data_quality_status=DataQuality.VALID,
        now=now,
        known_limitations=("test fixture",),
    )
    rebalance = build_rebalance_proposal(proposal, snapshot=snapshot, now=now)
    return proposal, rebalance, snapshot, exposure, policy


def make_validation_context(
    *,
    now: datetime = NOW,
    **overrides: object,
) -> ValidationContext:
    values: dict[str, object] = {
        "now": now,
        "execution_mode": ExecutionMode.LIVE,
        "human_approval_ref": "approval-test-1",
    }
    values.update(overrides)
    return ValidationContext(**values)  # type: ignore[arg-type]


def make_validation_policy(**overrides: object) -> PortfolioValidationPolicy:
    values: dict[str, object] = {
        "policy_version": "test-validation-1",
        "max_proposal_age_seconds": 900,
        "max_snapshot_age_seconds": 300,
        "require_complete_snapshot": True,
        "require_reconciled": True,
        "allow_risk_reducing_when_degraded": True,
        "require_stress_pass": True,
        "require_human_approval_for_increase": True,
        "require_watchdog_clear": True,
        "require_kill_switch_clear": True,
        "allow_paper_only_in_paper": True,
    }
    values.update(overrides)
    return PortfolioValidationPolicy(**values)  # type: ignore[arg-type]


def make_option_position(*, now: datetime = NOW) -> PositionRecord:
    return PositionRecord(
        position_id="pos_call_a",
        instrument_id="call_a",
        section=Section.OPTIONS,
        venue="test",
        quantity=1.0,
        multiplier=100.0,
        price=5.0,
        currency="USD",
        market_value=500.0,
        unrealized_pnl=0.0,
        as_of=now,
        source="test_source",
        valuation_method="black_scholes",
        quality=DataQuality.VALID,
        reconciliation_status=ReconciliationStatus.RECONCILED,
        option=OptionRiskInput(
            underlying_id="stock_a",
            underlying_price=100.0,
            strike=100.0,
            time_to_expiry_years=0.5,
            risk_free_rate=0.05,
            volatility=0.20,
            delta=0.55,
            gamma=0.02,
            vega=20.0,
            theta=-5.0,
            rho=10.0,
            model_version="test-bs-1",
            option_right="call",
            max_loss_per_contract=5.0,
            margin_per_contract=10.0,
            exercise_style="european",
        ),
    )


def make_prediction_position(*, now: datetime = NOW) -> PositionRecord:
    return PositionRecord(
        position_id="pos_event_1",
        instrument_id="event_1",
        section=Section.PREDICTIONS,
        venue="test",
        quantity=10.0,
        multiplier=1.0,
        price=0.40,
        currency="USD",
        market_value=4.0,
        unrealized_pnl=0.0,
        as_of=now,
        source="test_source",
        valuation_method="payoff_schedule",
        quality=DataQuality.VALID,
        reconciliation_status=ReconciliationStatus.RECONCILED,
        prediction=PredictionRiskInput(
            event_id="event_1",
            outcome_id="yes",
            entry_price_per_contract=0.40,
            outcomes=(
                PredictionOutcome("yes", 1.0),
                PredictionOutcome("no", 0.0),
            ),
            platform="test",
        ),
    )


def make_prediction_registry() -> InstrumentRegistry:
    registry = make_registry()
    registry.register(
        InstrumentSpec(
            instrument_id="event_1",
            symbol="EVENT1",
            section=Section.PREDICTIONS,
            asset_class=AssetClass.PREDICTION,
            instrument_type=InstrumentType.BINARY_CONTRACT,
            venue="test",
            currency="USD",
            settlement_currency="USD",
            event_id="event_1",
        )
    )
    return registry


def make_fx_rate(*, now: datetime = NOW) -> FxRate:
    return FxRate(
        base_currency="EUR",
        quote_currency="USD",
        rate=1.10,
        as_of=now,
        source="test_fx",
    )


def make_capital_summary(*, equity: float = 100_000.0) -> CapitalSummary:
    return CapitalSummary(
        base_currency="USD",
        cash_balance=equity,
        equity_value=equity,
        account_equity=equity,
        reserved_order_capacity=0.0,
        margin_requirement=0.0,
        available_to_trade=equity,
        commitments_not_reserved=0.0,
        unsettled_proceeds=0.0,
        risk_budget_remaining=equity,
        liquidation_value_estimate=equity,
        conversion_complete=True,
    )


def make_source_ref(*, now: datetime = NOW) -> SourceRef:
    return SourceRef(
        source_id="test_source",
        source_type="test",
        observed_at=now,
        received_at=now,
        quality=DataQuality.VALID,
    )


def constraint_names(
    instrument_ids: tuple[str, str] = ("stock_a", "stock_b"),
    weights: tuple[float, ...] = (0.5, 0.5),
    current_weights: tuple[float, ...] = (0.1, 0.1),
    policy: RiskBudgetPolicy | None = None,
) -> tuple[str, ...]:
    policy = policy or make_policy()
    results = evaluate_allocation_constraints(
        instrument_ids=instrument_ids,
        weights=weights,
        current_weights=current_weights,
        covariance=make_covariance(),
        policy=policy,
        equity=100_000.0,
        section_by_instrument={"stock_a": Section.STOCKS, "stock_b": Section.STOCKS},
        stress_passed=True,
    )
    return tuple(result.name for result in results)
