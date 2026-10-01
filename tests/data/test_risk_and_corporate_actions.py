"""Ex-ante tail risk, corporate actions, survivorship and the factor registry."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from axel.data.features.factor_registry import (
    RESEARCH_REGISTRY,
    FactorFamily,
    FactorRegistry,
    FactorSpec,
    RetestStatus,
)
from axel.data.ml.corporate_actions import (
    Delisting,
    Dividend,
    ListingRecord,
    PointInTimeUniverse,
    TerminalPricePolicy,
    apply_corporate_actions,
    apply_dividends,
    dividend_cash,
    dividends_in_window,
)
from axel.data.ml.risk_model import (
    FixedFractionalRiskModel,
    StopDistanceRiskModel,
    VolatilityRiskModel,
    realized_volatility,
)
from axel.data.ml.splits import Split
from axel.data.schemas import BarRecord, Provenance
from axel.risk.tail import (
    SCENARIOS,
    Position,
    ProposedTrade,
    RiskBudget,
    TailRiskEngine,
    expected_shortfall,
    historical_var,
    marginal_var,
    stress_loss,
)
from axel.util.stats import log_returns, sample_std

UTC_T0 = datetime(2026, 1, 5, tzinfo=UTC)


def _provenance(index: int = 0) -> Provenance:
    return Provenance(
        source="test",
        source_id=str(index),
        source_hash=f"h{index}",
        ingestion_timestamp=UTC_T0,
    )


def _bars(
    symbol: str = "AAA",
    count: int = 6,
    *,
    start_price: float = 100.0,
    step: float = 1.0,
    daily: bool = True,
) -> list[BarRecord]:
    return [
        BarRecord(
            symbol=symbol,
            venue="test",
            event_time=UTC_T0 + timedelta(days=index if daily else index * 7),
            available_at=UTC_T0 + timedelta(days=(index + 1) if daily else (index + 1) * 7),
            open=start_price + step * index,
            high=start_price + step * index + 1,
            low=start_price + step * index - 1,
            close=start_price + step * index + 0.5,
            volume=1_000_000.0,
            adjustment_status="raw",
            provenance=_provenance(index),
        )
        for index in range(count)
    ]


def _returns(volatility: float, count: int = 60, drift: float = 0.0003, seed: int = 0) -> list[float]:
    """Deterministic pseudo-returns (no RNG dependency in tests)."""
    values: list[float] = []
    state = seed + 1
    for _ in range(count):
        state = (1103515245 * state + 12345) % (2**31)
        shock = (state / (2**31) - 0.5) * 2.0
        values.append(drift + shock * volatility)
    return values


# --- Tail risk --------------------------------------------------------------


class TestTailMeasures:
    def test_var_is_a_positive_loss_fraction(self):
        series = [-0.05, -0.02, 0.01, 0.02, 0.03, 0.01, -0.01, 0.04]
        assert historical_var(series, 0.95) > 0
        assert historical_var([-0.01, 0.01, 0.02], 0.95) >= 0

    def test_expected_shortfall_exceeds_var(self):
        """The tail mean must be worse than the threshold it is averaged past."""
        series = _returns(0.03, 200) + [-0.30, -0.25]
        assert expected_shortfall(series, 0.95) >= historical_var(series, 0.95)

    def test_empty_series_is_zero_not_an_error(self):
        assert historical_var([], 0.95) == 0.0
        assert expected_shortfall([], 0.95) == 0.0

    def test_more_volatile_series_has_higher_var(self):
        calm = _returns(0.005, 200)
        wild = _returns(0.05, 200)
        assert historical_var(wild) > historical_var(calm)

    def test_marginal_var_rewards_diversification(self):
        portfolio = _returns(0.02, 120)
        correlated = portfolio
        uncorrelated = [value * 0.1 for value in portfolio]
        assert marginal_var(portfolio, uncorrelated) < marginal_var(portfolio, correlated)

    def test_marginal_var_is_zero_without_history(self):
        assert marginal_var([], [0.01]) == 0.0


class TestStressScenarios:
    def test_every_named_scenario_exists(self):
        budget = RiskBudget()
        for name in budget.scenarios:
            assert name in SCENARIOS

    def test_unknown_scenario_is_rejected(self):
        with pytest.raises(ValueError, match="unknown stress scenario"):
            RiskBudget(scenarios=("meteorite",))

    def test_stress_loss_scales_with_weight(self):
        position = Position("AAA", 10, 100.0, "tech")
        engine = TailRiskEngine()
        small = engine.calculate_stress_loss(position, "covid_2020", equity=100_000.0)
        big = engine.calculate_stress_loss(
            Position("AAA", 100, 100.0, "tech"), "covid_2020", equity=100_000.0
        )
        assert big == pytest.approx(small * 10, rel=1e-6)

    def test_single_name_default_is_a_total_loss(self):
        position = Position("AAA", 50, 100.0, "tech")  # 5% of a 100k book
        loss = stress_loss(position, SCENARIOS["single_name_default"], 100_000.0)
        assert loss == pytest.approx(0.05)

    def test_mark_to_zero_ignores_last_price(self):
        event = Delisting("AAA", date(2026, 3, 1))
        assert event.terminal_price(42.0) == 0.0
        last_trade = Delisting(
            "AAA", date(2026, 3, 1), terminal_price_policy=TerminalPricePolicy.LAST_TRADE
        )
        assert last_trade.terminal_price(42.0) == 42.0


class TestPositionLimits:
    def test_sizing_is_inverse_to_volatility_not_a_fixed_percentage(self):
        """The pack's core rule: size follows volatility, not a fixed 1%/2%."""
        engine = TailRiskEngine()
        portfolio = [Position("BOTTOM", 10, 100.0, "other")]
        sizes = {}
        for volatility in (0.005, 0.01, 0.02, 0.04):
            series = _returns(volatility, 60)
            decision = engine.calculate_position_limit(
                ProposedTrade("NEW", 1000, 100.0, "new"),
                portfolio,
                equity=1_000_000.0,
                asset_returns={"NEW": series, "BOTTOM": series},
            )
            sizes[volatility] = decision.approved_notional
        ordered = [sizes[v] for v in (0.005, 0.01, 0.02, 0.04)]
        assert ordered == sorted(ordered, reverse=True), "higher vol must not get more size"

    def test_asset_exposure_cap_binds(self):
        engine = TailRiskEngine(RiskBudget(max_asset_exposure=0.02))
        decision = engine.calculate_position_limit(
            ProposedTrade("NEW", 1000, 100.0, "tech"),
            [],
            equity=1_000_000.0,
            asset_returns={"NEW": _returns(0.001, 60)},
        )
        assert decision.approved_notional <= 20_000.0 + 1e-6
        assert decision.binding_constraint == "ASSET_EXPOSURE_CAP"

    def test_full_sector_blocks_additional_exposure(self):
        engine = TailRiskEngine(RiskBudget(max_sector_exposure=0.20))
        portfolio = [Position("AAA", 200, 100.0, "tech")]  # 20% of a 100k book
        decision = engine.calculate_position_limit(
            ProposedTrade("BBB", 100, 100.0, "tech"),
            portfolio,
            equity=100_000.0,
            asset_returns={"BBB": _returns(0.02, 60)},
        )
        assert decision.approved_notional == pytest.approx(0.0)
        assert decision.binding_constraint == "SECTOR_EXPOSURE_CAP"

    def test_new_sector_is_not_blocked_by_the_sector_cap(self):
        """A different sector must not be constrained by a full tech sector."""
        engine = TailRiskEngine(RiskBudget(max_sector_exposure=0.20))
        portfolio = [Position("AAA", 50, 100.0, "tech")]  # 5% of a 100k book
        decision = engine.calculate_position_limit(
            ProposedTrade("CCC", 10, 100.0, "energy"),
            portfolio,
            equity=100_000.0,
            asset_returns={"CCC": _returns(0.02, 60)},
        )
        assert decision.approved_notional > 0.0
        assert decision.binding_constraint != "SECTOR_EXPOSURE_CAP"

    def test_book_already_over_the_stress_ceiling_blocks_new_risk(self):
        """Once the worst-case loss is spent, no further notional is granted."""
        engine = TailRiskEngine(RiskBudget(max_stress_loss=0.10))
        portfolio = [Position("AAA", 200, 100.0, "tech")]  # 20% single-name weight
        decision = engine.calculate_position_limit(
            ProposedTrade("CCC", 10, 100.0, "energy"),
            portfolio,
            equity=100_000.0,
            asset_returns={"CCC": _returns(0.02, 60)},
        )
        assert decision.approved_notional == pytest.approx(0.0)
        assert decision.binding_constraint == "STRESS_LOSS_CAP"

    def test_stress_cap_shrinks_a_size_that_would_blow_the_ceiling(self):
        # Tighter than the 5% single-name cap, so stress loss binds first.
        engine = TailRiskEngine(RiskBudget(max_stress_loss=0.02))
        decision = engine.calculate_position_limit(
            ProposedTrade("CCC", 1000, 100.0, "energy"),
            [],
            equity=100_000.0,
            asset_returns={"CCC": _returns(0.02, 60)},
        )
        assert 0.0 < decision.approved_notional < 20_000.0
        assert decision.binding_constraint == "STRESS_LOSS_CAP"

    def test_leverage_cap_blocks_when_book_is_full(self):
        engine = TailRiskEngine(RiskBudget(max_leverage=1.0, max_total_exposure=1.0))
        portfolio = [Position("AAA", 1000, 100.0, "tech")]  # exactly 100% of equity
        decision = engine.calculate_position_limit(
            ProposedTrade("BBB", 10, 100.0, "energy"),
            portfolio,
            equity=100_000.0,
            asset_returns={"BBB": _returns(0.02, 60)},
        )
        assert decision.approved_notional == pytest.approx(0.0)

    def test_invalid_equity_is_rejected(self):
        engine = TailRiskEngine()
        decision = engine.calculate_position_limit(
            ProposedTrade("AAA", 10, 100.0, "tech"), [], equity=0.0, asset_returns={}
        )
        assert decision.binding_constraint == "INVALID_INPUTS"
        assert not decision.approved

    def test_checks_reveal_the_binding_constraint(self):
        engine = TailRiskEngine()
        decision = engine.calculate_position_limit(
            ProposedTrade("NEW", 10, 100.0, "tech"),
            [],
            equity=100_000.0,
            asset_returns={"NEW": _returns(0.02, 60)},
        )
        assert {name for name, _, _ in decision.checks} >= {
            "RISK_FRACTION_CAP",
            "ASSET_EXPOSURE_CAP",
            "SECTOR_EXPOSURE_CAP",
            "LEVERAGE_CAP",
        }


class TestTailRiskReport:
    def test_empty_portfolio_reports_zero_risk(self):
        report = TailRiskEngine().evaluate_portfolio([], equity=100_000.0, asset_returns={})
        assert report.portfolio_var == 0.0
        assert report.total_exposure == 0.0
        assert report.within_budget(RiskBudget())

    def test_normal_and_extreme_layers_are_separate(self):
        engine = TailRiskEngine()
        series = _returns(0.02, 120)
        report = engine.evaluate_portfolio(
            [Position("AAA", 50, 100.0, "tech")],
            equity=100_000.0,
            asset_returns={"AAA": series},
        )
        assert report.portfolio_var > 0
        assert report.expected_shortfall >= report.portfolio_var
        assert report.worst_stress_loss > 0
        assert report.worst_scenario in SCENARIOS

    def test_breaches_are_reported_not_raised(self):
        engine = TailRiskEngine()
        series = _returns(0.05, 120)
        report = engine.evaluate_portfolio(
            [Position("AAA", 900, 100.0, "tech")],
            equity=100_000.0,
            asset_returns={"AAA": series},
        )
        tight = RiskBudget(max_asset_exposure=0.01, max_stress_loss=0.01)
        names = {name for name, _, _ in report.breaches(tight)}
        assert "max_stress_loss" in names
        assert not report.within_budget(tight)

    def test_budget_validation_rejects_nonsense(self):
        with pytest.raises(ValueError):
            RiskBudget(risk_fraction=0.0)
        with pytest.raises(ValueError):
            RiskBudget(confidence=1.0)
        with pytest.raises(ValueError):
            RiskBudget(min_history=1)
        with pytest.raises(ValueError):
            RiskBudget(max_asset_exposure=-0.1)

    def test_correlation_detected_across_assets(self):
        engine = TailRiskEngine()
        series = _returns(0.02, 120)
        report = engine.evaluate_portfolio(
            [Position("AAA", 50, 100.0, "tech"), Position("BBB", 50, 100.0, "tech")],
            equity=100_000.0,
            asset_returns={"AAA": series, "BBB": series},
        )
        assert report.average_correlation > 0.99


# --- Risk models ------------------------------------------------------------


class TestRiskModels:
    def test_volatility_risk_scales_with_position_and_volatility(self):
        history = _bars(count=30, start_price=100.0, step=5.0)
        low = VolatilityRiskModel(lookback=30).risk(
            symbol="AAA", quantity=10, price=100.0, history=history
        )
        high = VolatilityRiskModel(lookback=30).risk(
            symbol="AAA", quantity=100, price=100.0, history=history
        )
        assert high == pytest.approx(low * 10, rel=1e-9)

    def test_volatility_risk_is_positive_only(self):
        history = _bars(count=30)
        model = VolatilityRiskModel(lookback=30)
        long = model.risk(symbol="A", quantity=10, price=100.0, history=history)
        short = model.risk(symbol="A", quantity=-10, price=100.0, history=history)
        assert long == pytest.approx(short)

    def test_flat_history_uses_the_floor_not_zero(self):
        flat = [BarRecord(
            symbol="AAA", venue="t", event_time=UTC_T0 + timedelta(days=i),
            available_at=UTC_T0 + timedelta(days=i + 1), open=100.0, high=100.0,
            low=100.0, close=100.0, volume=1.0, adjustment_status="raw",
            provenance=_provenance(i),
        ) for i in range(30)]
        risk = VolatilityRiskModel(lookback=30, floor_vol=1e-4).risk(
            symbol="AAA", quantity=10, price=100.0, history=flat
        )
        assert risk > 0.0

    def test_stop_distance_matches_kelly_convention(self):
        history = _bars(count=10)
        risk = StopDistanceRiskModel(stop_fraction=0.02).risk(
            symbol="AAA", quantity=100, price=50.0, history=history
        )
        assert risk == pytest.approx(100 * 50.0 * 0.02)

    def test_fixed_fractional_is_available_but_is_not_the_default(self):
        model = FixedFractionalRiskModel(0.01)
        risk = model.risk(symbol="A", quantity=100, price=10.0, history=())
        assert risk == pytest.approx(10.0)

    def test_realized_volatility_matches_manual_computation(self):
        history = _bars(count=30, step=1.0)
        expected = sample_std(log_returns([bar.close for bar in history[-30:]]))
        assert realized_volatility(history, 30) == pytest.approx(expected)

    def test_model_validation(self):
        with pytest.raises(ValueError):
            VolatilityRiskModel(lookback=1)
        with pytest.raises(ValueError):
            VolatilityRiskModel(horizon_bars=0)
        with pytest.raises(ValueError):
            StopDistanceRiskModel(stop_fraction=0.0)
        with pytest.raises(ValueError):
            FixedFractionalRiskModel(-0.1)


# --- Corporate actions ------------------------------------------------------


class TestDividends:
    def test_backward_adjustment_makes_the_ex_date_return_neutral(self):
        bars = _bars(symbol="AAA", count=4, start_price=100.0)
        dividend = Dividend("AAA", date(2026, 1, 7), amount=5.0)  # ex-date = bar 2
        adjusted = apply_dividends(bars, [dividend])
        before = adjusted[1].close
        on_ex = adjusted[2].close
        raw_before, raw_ex = bars[1].close, bars[2].close
        raw_return = raw_ex / raw_before - 1.0
        adjusted_return = on_ex / before - 1.0
        assert adjusted_return == pytest.approx(raw_return + 0.05, abs=0.01)

    def test_adjustment_is_idempotent(self):
        bars = _bars(symbol="AAA", count=4)
        dividend = Dividend("AAA", date(2026, 1, 7), amount=1.0)
        once = apply_dividends(bars, [dividend])
        twice = apply_dividends(once, [dividend])
        assert [bar.close for bar in twice] == [bar.close for bar in once]
        flags = [f for f in once[0].provenance.quality_flags if f.startswith("dividend")]
        assert len(flags) == 1

    def test_adjustment_only_touches_pre_ex_bars(self):
        bars = _bars(symbol="AAA", count=4)
        adjusted = apply_dividends(bars, [Dividend("AAA", date(2026, 1, 7), 1.0)])
        assert adjusted[-1].close == bars[-1].close

    def test_impossible_dividend_is_rejected(self):
        bars = _bars(symbol="AAA", count=4, start_price=2.0, step=0.1)
        with pytest.raises(ValueError, match="not payable"):
            apply_dividends(bars, [Dividend("AAA", date(2026, 1, 7), 10.0)])

    def test_dividend_validation(self):
        with pytest.raises(ValueError):
            Dividend("", date(2026, 1, 1), 1.0)
        with pytest.raises(ValueError):
            Dividend("AAA", date(2026, 1, 1), -1.0)
        with pytest.raises(ValueError):
            Dividend("AAA", date(2026, 1, 5), 1.0, pay_date=date(2026, 1, 1))

    def test_cash_credit_scales_with_shares(self):
        assert dividend_cash(100, 0.5) == pytest.approx(50.0)

    def test_window_selection_is_half_open(self):
        dividends = [
            Dividend("AAA", date(2026, 1, 2), 1.0),
            Dividend("AAA", date(2026, 1, 9), 1.0),
        ]
        inside = dividends_in_window(dividends, date(2026, 1, 2), date(2026, 1, 9))
        assert [d.ex_date.day for d in inside] == [9]

    def test_combined_split_then_dividend(self):
        # Bars run Jan 5..8 with available_at one day later. A split effective
        # Jan 7 adjusts Jan 5-6; a dividend with an ex-date of Jan 7 adjusts
        # Jan 5-6 too, so the status records both and Jan 7-8 stay raw.
        bars = _bars(symbol="AAA", count=4)
        result = apply_corporate_actions(
            bars,
            splits=[Split("AAA", date(2026, 1, 7), 2.0)],
            dividends=[Dividend("AAA", date(2026, 1, 7), 1.0)],
        )
        assert result[0].close < bars[0].close
        assert result[0].adjustment_status == "split_and_dividend_adjusted"
        assert result[-1].close == bars[-1].close
        assert result[-1].adjustment_status == "raw"

    def test_dividend_ex_date_before_the_first_bar_changes_nothing(self):
        """An ex-date earlier than every bar is not payable to this holding."""
        bars = _bars(symbol="AAA", count=4)
        result = apply_dividends(bars, [Dividend("AAA", date(2025, 12, 1), 1.0)])
        assert [bar.close for bar in result] == [bar.close for bar in bars]
        assert all(bar.adjustment_status == "raw" for bar in result)


class TestPointInTimeUniverse:
    @staticmethod
    def _universe() -> PointInTimeUniverse:
        return PointInTimeUniverse([
            ListingRecord("AAA", date(2010, 1, 4)),
            ListingRecord("BANKRUPT", date(2010, 1, 4), delisted_on=date(2019, 6, 3), is_active=False),
            ListingRecord("NEWCO", date(2020, 3, 2)),
            ListingRecord("LATE", date(2021, 1, 4), delisted_on=date(2022, 2, 2), is_active=False),
        ])

    def test_delisted_name_is_present_before_and_absent_after(self):
        universe = self._universe()
        assert "BANKRUPT" in universe.members(date(2018, 6, 1))
        assert "BANKRUPT" not in universe.members(date(2019, 6, 3))

    def test_survivorship_bias_is_measurable(self):
        universe = self._universe()
        dropped = set(universe.survivorship_bias(date(2018, 6, 1)))
        members = set(universe.members(date(2018, 6, 1)))
        survivors = set(universe.survivors_only(date(2018, 6, 1)))
        assert dropped == members - survivors
        assert "BANKRUPT" in dropped

    def test_biased_view_shrinks_the_universe(self):
        universe = self._universe()
        assert len(universe.members(date(2018, 6, 1))) == 2  # AAA + BANKRUPT
        assert len(universe.survivors_only(date(2018, 6, 1))) == 1  # AAA only

    def test_pre_listing_symbol_is_not_a_member(self):
        universe = self._universe()
        assert "NEWCO" not in universe.members(date(2019, 1, 1))
        assert "NEWCO" in universe.members(date(2021, 1, 1))

    def test_tradable_and_membership_history(self):
        universe = self._universe()
        assert universe.is_tradable("AAA", date(2015, 1, 1))
        assert not universe.is_tradable("BANKRUPT", date(2020, 1, 1))
        assert not universe.is_tradable("NOPE", date(2020, 1, 1))
        assert universe.membership_history("LATE") == (date(2021, 1, 4), date(2022, 2, 2))

    def test_listing_validation(self):
        with pytest.raises(ValueError):
            ListingRecord("AAA", date(2020, 1, 1), delisted_on=date(2019, 1, 1))


# --- Factor registry --------------------------------------------------------


class TestFactorRegistry:
    def test_no_seeded_factor_is_live_ready(self):
        assert RESEARCH_REGISTRY.live_ready() == ()
        assert len(RESEARCH_REGISTRY) >= 15

    def test_all_three_families_are_registered(self):
        for family in FactorFamily:
            assert RESEARCH_REGISTRY.by_family(family)

    def test_missing_provenance_is_rejected(self):
        with pytest.raises(ValueError, match="source_paper"):
            FactorSpec(
                factor_id="x", family=FactorFamily.RISK, source_paper="",
                source_year=2007, source_market="US", source_period="x",
                source_population="x", feature_definition="x", target_definition="t",
                prediction_horizon="h", validation_method="v", reported_metric="m",
                known_limitations="l", point_in_time_requirement="p",
            )

    def test_promotion_requires_axel_own_evidence(self):
        """A paper's metric must never be sufficient to mark a factor proven."""
        with pytest.raises(ValueError, match="requires"):
            FactorSpec(
                factor_id="x", family=FactorFamily.RISK, source_paper="p",
                source_year=2007, source_market="US", source_period="s",
                source_population="s", feature_definition="f", target_definition="t",
                prediction_horizon="h", validation_method="v", reported_metric="m",
                known_limitations="l", point_in_time_requirement="p",
                retest_status=RetestStatus.PIT_RETESTED,
            )

    def test_retest_with_evidence_promotes(self):
        spec = RESEARCH_REGISTRY.get("delisting_roe").record_retest(
            retest_date=date(2026, 1, 1),
            oos_trades=250,
            deflated_sharpe=0.96,
            fingerprint="a" * 64,
        )
        assert spec.retest_status is RetestStatus.PIT_RETESTED
        assert spec.usable_live

    def test_zero_trades_does_not_count_as_evidence(self):
        with pytest.raises(ValueError, match="axel_oos_trades"):
            RESEARCH_REGISTRY.get("delisting_roe").record_retest(
                retest_date=date(2026, 1, 1), oos_trades=0,
                deflated_sharpe=0.96, fingerprint="b" * 64,
            )

    def test_reject_is_terminal_for_use(self):
        spec = RESEARCH_REGISTRY.get("risk_concentration").reject(reason="no effect")
        assert spec.retest_status is RetestStatus.REJECTED
        assert not spec.usable_live

    def test_duplicate_ids_are_rejected(self):
        duplicate = RESEARCH_REGISTRY.get("delisting_roe")
        with pytest.raises(ValueError, match="duplicate"):
            FactorRegistry.from_specs([duplicate, duplicate])

    def test_registry_is_immutable(self):
        with pytest.raises((AttributeError, TypeError)):
            RESEARCH_REGISTRY.factors[0].factor_id = "mutated"  # type: ignore[misc]

    def test_pit_requirement_is_recorded_for_every_factor(self):
        for spec in RESEARCH_REGISTRY:
            assert "available_at" in spec.point_in_time_requirement
            assert spec.reported_metric