"""Tests for Phase 14 PSI and drift detection."""

from __future__ import annotations

import pytest

from axel.research.drift import (
    DriftMonitor,
    DriftReport,
    FeatureDriftResult,
    feature_drift_report,
    population_stability_index,
    psi_alert_level,
)


# --- PSI computation ---


class TestPSI:
    def test_identical_distributions_have_near_zero_psi(self):
        data = [float(i) for i in range(200)]
        psi = population_stability_index(data, data)
        assert psi < 0.01

    def test_shifted_distribution_has_higher_psi(self):
        reference = [float(i) for i in range(200)]
        shifted = [float(i + 50) for i in range(200)]
        psi = population_stability_index(reference, shifted)
        assert psi > 0.0

    def test_very_different_distributions_have_high_psi(self):
        reference = [float(i) for i in range(200)]
        different = [float(i * 10 + 500) for i in range(200)]
        psi = population_stability_index(reference, different)
        assert psi > 0.10

    def test_empty_input_returns_zero(self):
        assert population_stability_index([], [1.0, 2.0]) == 0.0
        assert population_stability_index([1.0, 2.0], []) == 0.0
        assert population_stability_index([], []) == 0.0

    def test_psi_is_non_negative(self):
        ref = [float(i) for i in range(100)]
        act = [float(i * 2) for i in range(100)]
        psi = population_stability_index(ref, act)
        assert psi >= 0.0

    def test_single_value_distributions(self):
        psi = population_stability_index([1.0] * 50, [1.0] * 50)
        assert psi >= 0.0  # should not crash

    def test_custom_bins(self):
        ref = [float(i) for i in range(200)]
        act = [float(i + 10) for i in range(200)]
        psi5 = population_stability_index(ref, act, n_bins=5)
        psi20 = population_stability_index(ref, act, n_bins=20)
        # Both should be non-negative
        assert psi5 >= 0.0
        assert psi20 >= 0.0

    def test_smoothing_prevents_log_zero(self):
        # Edge case: all actual values in one bin, empty bins elsewhere
        ref = [float(i) for i in range(100)]
        extreme = [50.0] * 100
        psi = population_stability_index(ref, extreme)
        assert psi >= 0.0
        assert not float("inf") == psi


# --- PSI alert levels ---


class TestPSIAlertLevel:
    def test_stable(self):
        assert psi_alert_level(0.05) == "stable"

    def test_moderate(self):
        assert psi_alert_level(0.15) == "moderate_drift"

    def test_significant(self):
        assert psi_alert_level(0.30) == "significant_drift"

    def test_boundary_stable(self):
        assert psi_alert_level(0.09) == "stable"

    def test_boundary_moderate(self):
        assert psi_alert_level(0.10) == "moderate_drift"

    def test_boundary_significant(self):
        assert psi_alert_level(0.25) == "significant_drift"


# --- Feature drift report ---


class TestFeatureDriftReport:
    def test_report_with_identical_features(self):
        ref = {"price": [float(i) for i in range(100)]}
        report = feature_drift_report(ref, ref)
        assert report.overall_alert == "stable"
        assert len(report.features_with_drift) == 0

    def test_report_detects_drifted_feature(self):
        ref = {"price": [float(i) for i in range(200)]}
        cur = {"price": [float(i * 10 + 500) for i in range(200)]}
        report = feature_drift_report(ref, cur)
        assert len(report.feature_results) == 1
        # Should detect some level of drift
        assert report.feature_results[0].psi > 0.0

    def test_report_handles_multiple_features(self):
        ref = {
            "a": [float(i) for i in range(100)],
            "b": [float(i) for i in range(100)],
        }
        cur = {
            "a": [float(i) for i in range(100)],  # same
            "b": [float(i + 200) for i in range(100)],  # shifted
        }
        report = feature_drift_report(ref, cur)
        assert len(report.feature_results) == 2

    def test_report_only_compares_common_features(self):
        ref = {"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]}
        cur = {"a": [1.0, 2.0, 3.0], "c": [7.0, 8.0, 9.0]}
        report = feature_drift_report(ref, cur)
        assert len(report.feature_results) == 1
        assert report.feature_results[0].feature_name == "a"

    def test_report_includes_statistics(self):
        ref = {"x": [1.0, 2.0, 3.0, 4.0, 5.0] * 20}
        cur = {"x": [10.0, 20.0, 30.0, 40.0, 50.0] * 20}
        report = feature_drift_report(ref, cur)
        result = report.feature_results[0]
        assert result.reference_mean > 0
        assert result.current_mean > 0
        assert result.reference_std >= 0
        assert result.current_std >= 0

    def test_empty_report(self):
        report = feature_drift_report({}, {})
        assert report.overall_alert == "stable"
        assert report.summary == "no features compared"


# --- DriftMonitor ---


class TestDriftMonitor:
    def test_set_reference_and_check(self):
        monitor = DriftMonitor()
        ref = {"feat1": [float(i) for i in range(100)]}
        monitor.set_reference(ref)
        report = monitor.check(ref)
        assert report.overall_alert == "stable"

    def test_history_accumulates(self):
        monitor = DriftMonitor()
        ref = {"feat1": [float(i) for i in range(100)]}
        monitor.set_reference(ref)
        monitor.check(ref)
        monitor.check(ref)
        assert len(monitor.history) == 2

    def test_reference_features_are_listed(self):
        monitor = DriftMonitor()
        monitor.set_reference({"a": [1.0], "b": [2.0]})
        assert monitor.reference_features == ["a", "b"]

    def test_custom_bins_and_epsilon(self):
        monitor = DriftMonitor(n_bins=5, epsilon=1e-6)
        ref = {"x": [float(i) for i in range(50)]}
        monitor.set_reference(ref)
        report = monitor.check(ref)
        assert report.overall_alert == "stable"
