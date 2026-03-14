"""Tests for specification health vector."""

from __future__ import annotations

import pytest

from lintgate.specification.health_vector import (
    HealthAxis,
    SpecificationHealth,
    VetoGate,
    _geometric_mean,
    compute_health,
)


class TestGeometricMean:
    def test_all_ones(self):
        assert _geometric_mean([1.0, 1.0, 1.0]) == pytest.approx(1.0)

    def test_mixed_values(self):
        expected = (0.8 * 0.6 * 0.4) ** (1 / 3)
        assert _geometric_mean([0.8, 0.6, 0.4]) == pytest.approx(expected)

    def test_zero_returns_zero(self):
        assert _geometric_mean([1.0, 0.0, 1.0]) == 0.0

    def test_empty_returns_zero(self):
        assert _geometric_mean([]) == 0.0

    def test_single_value(self):
        assert _geometric_mean([0.7]) == pytest.approx(0.7)


class TestComputeHealth:
    def test_perfect_health(self):
        h = compute_health(1.0, 1.0, 1.0, 0.0, 1.0)
        assert h.scalar == pytest.approx(1.0)
        assert h.vetoed is False
        assert all(v == 1.0 for v in h.axes.values())

    def test_geometric_mean_correct(self):
        h = compute_health(0.9, 0.8, 0.7, 0.0, 0.6)
        # composition_gamma=0.0 → composition=1.0
        expected = (0.9 * 0.8 * 0.7 * 1.0 * 0.6) ** (1 / 5)
        assert h.scalar == pytest.approx(expected, abs=0.01)

    def test_zero_axis_kills_scalar(self):
        h = compute_health(1.0, 0.0, 1.0, 0.0, 1.0)
        assert h.scalar == 0.0

    def test_high_gamma_low_composition(self):
        h = compute_health(1.0, 1.0, 1.0, 5.0, 1.0)
        assert h.axes[HealthAxis.COMPOSITION.value] == pytest.approx(1 / 6, abs=0.01)
        # Scalar should be pulled down by low composition
        assert h.scalar < 0.7

    def test_gamma_zero_full_composition(self):
        h = compute_health(0.5, 0.5, 0.5, 0.0, 0.5)
        assert h.axes[HealthAxis.COMPOSITION.value] == pytest.approx(1.0)

    def test_all_axes_present(self):
        h = compute_health(0.5, 0.5, 0.5, 1.0, 0.5)
        assert set(h.axes.keys()) == {a.value for a in HealthAxis}

    def test_values_clamped(self):
        h = compute_health(1.5, -0.1, 0.5, 0.0, 0.5)
        assert h.axes[HealthAxis.SPEC_LEVEL.value] == 1.0
        assert h.axes[HealthAxis.KILL_RATE.value] == 0.0


class TestVetoGates:
    def test_discovery_artifact_vetoes(self):
        h = compute_health(1.0, 1.0, 1.0, 0.0, 1.0, has_discovery_artifact=True)
        assert h.vetoed is True
        assert h.vetoes[VetoGate.DISCOVERY_ARTIFACT.value] is True
        assert h.scalar == 0.0

    def test_mock_boundary_vetoes(self):
        h = compute_health(1.0, 1.0, 1.0, 0.0, 1.0, mock_boundary_share=0.6)
        assert h.vetoed is True
        assert h.vetoes[VetoGate.MOCK_BOUNDARY.value] is True
        assert h.scalar == 0.0

    def test_budget_instability_vetoes(self):
        h = compute_health(1.0, 1.0, 1.0, 0.0, 1.0, budget_exhausted_share=0.4)
        assert h.vetoed is True
        assert h.vetoes[VetoGate.BUDGET_INSTABILITY.value] is True

    def test_below_threshold_no_veto(self):
        h = compute_health(
            1.0, 1.0, 1.0, 0.0, 1.0, mock_boundary_share=0.3, budget_exhausted_share=0.2
        )
        assert h.vetoed is False
        assert h.scalar == pytest.approx(1.0)

    def test_custom_thresholds(self):
        h = compute_health(
            1.0, 1.0, 1.0, 0.0, 1.0, mock_boundary_share=0.3, mock_boundary_threshold=0.2
        )
        assert h.vetoes[VetoGate.MOCK_BOUNDARY.value] is True
        assert h.vetoed is True

    def test_no_vetoes_present(self):
        h = compute_health(0.5, 0.5, 0.5, 0.0, 0.5)
        assert all(v is False for v in h.vetoes.values())

    def test_all_veto_gates_present(self):
        h = compute_health(0.5, 0.5, 0.5, 0.0, 0.5)
        assert set(h.vetoes.keys()) == {g.value for g in VetoGate}


class TestUnmeasuredAxes:
    def test_unmeasured_convergence_excluded_from_mean(self):
        # convergence=0.0 with convergence_measured=False should not drag scalar to 0
        h = compute_health(0.8, 0.8, 0.0, 0.0, 0.8, convergence_measured=False)
        # scalar = gmean(0.8, 0.8, 1.0, 0.8) — convergence skipped, composition=1.0
        expected = (0.8 * 0.8 * 1.0 * 0.8) ** (1 / 4)
        assert h.scalar == pytest.approx(expected, abs=0.001)
        assert h.scalar > 0.0

    def test_all_unmeasured_returns_zero(self):
        # Only convergence measured and it's zero → scalar = 0.0
        h = compute_health(0.0, 0.0, 0.0, 0.0, 0.0, convergence_measured=True)
        assert h.scalar == 0.0

    def test_explicit_convergence_measured_overrides_inference(self):
        # convergence=0.5 but explicit convergence_measured=False → excludes convergence
        h = compute_health(0.8, 0.8, 0.5, 0.0, 0.8, convergence_measured=False)
        # scalar = gmean(0.8, 0.8, 1.0, 0.8) — convergence excluded despite non-zero value
        expected = (0.8 * 0.8 * 1.0 * 0.8) ** (1 / 4)
        assert h.scalar == pytest.approx(expected, abs=0.001)

    def test_backward_compat_default(self):
        # No convergence_measured param → convergence=0.0 inferred as unmeasured
        # (convergence > 0.0 is False) so convergence excluded from mean.
        # This fixes the old scalar collapse: previously convergence=0 killed
        # the geometric mean even when no convergence data existed.
        h = compute_health(0.8, 0.8, 0.0, 0.0, 0.8)
        # scalar = gmean(0.8, 0.8, 1.0, 0.8) — convergence excluded
        expected = (0.8 * 0.8 * 1.0 * 0.8) ** (1 / 4)
        assert h.scalar == pytest.approx(expected, abs=0.001)
        assert h.axes_measured[HealthAxis.CONVERGENCE.value] is False

    def test_axes_measured_populated(self):
        h = compute_health(0.8, 0.8, 0.0, 0.0, 0.8, convergence_measured=False)
        assert set(h.axes_measured.keys()) == {a.value for a in HealthAxis}
        assert h.axes_measured[HealthAxis.CONVERGENCE.value] is False
        assert h.axes_measured[HealthAxis.SPEC_LEVEL.value] is True
        assert h.axes_measured[HealthAxis.KILL_RATE.value] is True
        assert h.axes_measured[HealthAxis.COMPOSITION.value] is True
        assert h.axes_measured[HealthAxis.TEST_EFFICIENCY.value] is True

    def test_geometric_mean_with_measured_mask(self):
        # Direct test: gmean([0.8, 0.0, 0.6], measured=[True, False, True]) = gmean(0.8, 0.6)
        result = _geometric_mean([0.8, 0.0, 0.6], [True, False, True])
        expected = (0.8 * 0.6) ** 0.5
        assert result == pytest.approx(expected)


class TestEdgeCases:
    def test_all_zeros(self):
        h = compute_health(0.0, 0.0, 0.0, 0.0, 0.0)
        assert h.scalar == 0.0

    def test_returns_specification_health(self):
        h = compute_health(0.5, 0.5, 0.5, 0.0, 0.5)
        assert isinstance(h, SpecificationHealth)

    def test_scalar_rounded(self):
        h = compute_health(0.33, 0.33, 0.33, 0.0, 0.33)
        # Scalar should be rounded to 4 decimal places
        assert h.scalar == round(h.scalar, 4)
