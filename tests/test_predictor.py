"""Tests for lintgate.specification.predictor — decision tree and regime classification."""

from __future__ import annotations

import pytest

from lintgate.specification.predictor import (
    _build_trajectory,
    _classify_regime,
    _decision_tree,
    _detect_transition_point,
    detect_phase_from_trajectory,
    update_trajectory,
)
from lintgate.specification.types import TestDesignSignals, TrajectoryState


class TestClassifyRegime:
    """Multi-factor regime classification tests."""

    def test_pure_always_a(self):
        """Pure functions are always regime A regardless of sigma."""
        regime, rationale = _classify_regime(
            sigma=30, is_pure=True, semantic_ratio=0.0, weakness_taxonomy="WEAK"
        )
        assert regime == "A"
        assert "pure" in rationale

    def test_high_sigma_impure_is_b(self):
        """Impure functions with sigma > 20 → regime B."""
        regime, rationale = _classify_regime(
            sigma=21, is_pure=False, semantic_ratio=0.5, weakness_taxonomy=""
        )
        assert regime == "B"
        assert "21" in rationale

    def test_moderate_sigma_with_weakness_and_poor_spec_is_b(self):
        """Impure, sigma > 12, known weakness, poor coverage → regime B."""
        regime, rationale = _classify_regime(
            sigma=13, is_pure=False, semantic_ratio=0.2, weakness_taxonomy="WEAK"
        )
        assert regime == "B"
        assert "compounding" in rationale

    def test_moderate_sigma_without_weakness_is_a(self):
        """Moderate sigma without weakness → regime A."""
        regime, _rationale = _classify_regime(
            sigma=15, is_pure=False, semantic_ratio=0.2, weakness_taxonomy=""
        )
        assert regime == "A"

    def test_moderate_sigma_with_weakness_but_good_spec_is_a(self):
        """Moderate sigma with weakness but good coverage → regime A."""
        regime, _rationale = _classify_regime(
            sigma=15, is_pure=False, semantic_ratio=0.5, weakness_taxonomy="WEAK"
        )
        assert regime == "A"

    def test_low_sigma_always_a(self):
        """Low sigma is always A even with bad factors."""
        regime, _rationale = _classify_regime(
            sigma=10, is_pure=False, semantic_ratio=0.1, weakness_taxonomy="WEAK"
        )
        assert regime == "A"

    def test_boundary_sigma_20_is_a(self):
        """Sigma exactly 20 is still A (threshold is > 20)."""
        regime, _rationale = _classify_regime(
            sigma=20, is_pure=False, semantic_ratio=0.5, weakness_taxonomy=""
        )
        assert regime == "A"

    def test_boundary_sigma_12_not_b(self):
        """Sigma exactly 12 doesn't trigger multi-factor B (threshold is > 12)."""
        regime, _rationale = _classify_regime(
            sigma=12, is_pure=False, semantic_ratio=0.1, weakness_taxonomy="WEAK"
        )
        assert regime == "A"

    def test_healthy_weakness_not_counted(self):
        """HEALTHY weakness taxonomy is treated same as empty."""
        regime, _rationale = _classify_regime(
            sigma=15, is_pure=False, semantic_ratio=0.1, weakness_taxonomy="HEALTHY"
        )
        assert regime == "A"

    def test_rationale_always_nonempty(self):
        """Every classification path produces a rationale."""
        for sigma, is_pure, sr, wt in [
            (5, True, 0.5, ""),
            (25, False, 0.5, ""),
            (15, False, 0.1, "WEAK"),
            (5, False, 0.5, ""),
        ]:
            _regime, rationale = _classify_regime(
                sigma=sigma, is_pure=is_pure, semantic_ratio=sr, weakness_taxonomy=wt
            )
            assert rationale, f"Empty rationale for sigma={sigma}, pure={is_pure}"


class TestDecisionTree:
    """Decision tree sigma computation + regime integration."""

    def test_path1_well_specified_pure(self):
        sigma, regime, rationale = _decision_tree(
            is_pure=True,
            semantic_ratio=0.6,
            weakness_taxonomy="",
            ast_category_count=5,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 3  # max(branch_count, 1)
        assert regime == "A"
        assert rationale

    def test_path2_under_specified_pure(self):
        sigma, regime, rationale = _decision_tree(
            is_pure=True,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=5,
            branch_count=2,
            parameter_count=3,
        )
        assert sigma == 6  # branch + params + 1
        assert regime == "A"
        assert rationale

    def test_path3_pure_with_weakness(self):
        sigma, regime, rationale = _decision_tree(
            is_pure=True,
            semantic_ratio=0.6,
            weakness_taxonomy="WEAK",
            ast_category_count=5,
            branch_count=2,
            parameter_count=3,
        )
        assert sigma == 7  # branch + params + 2
        assert regime == "A"
        assert rationale

    def test_path4_tractable_impure(self):
        sigma, regime, rationale = _decision_tree(
            is_pure=False,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=6,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 9  # ast_cats + branches
        assert regime == "A"
        assert rationale

    def test_path5_hard_but_progressing(self):
        sigma, regime, rationale = _decision_tree(
            is_pure=False,
            semantic_ratio=0.6,
            weakness_taxonomy="",
            ast_category_count=10,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 15  # ast_cats + branches + params
        assert regime == "A"
        assert rationale

    def test_path6_hardest_regime_b(self):
        sigma, regime, rationale = _decision_tree(
            is_pure=False,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=12,
            branch_count=8,
            parameter_count=5,
        )
        assert sigma == 27  # ast_cats + branches + params + 2
        assert regime == "B"  # sigma > 20
        assert rationale

    def test_path6_multi_factor_regime_b(self):
        """Path 6 with moderate sigma but weakness + poor spec → B."""
        sigma, regime, rationale = _decision_tree(
            is_pure=False,
            semantic_ratio=0.2,
            weakness_taxonomy="WEAK",
            ast_category_count=9,
            branch_count=2,
            parameter_count=2,
        )
        assert sigma == 15  # ast_cats + branches + params + 2
        assert regime == "B"  # sigma > 12, weakness, semantic < 0.3
        assert "compounding" in rationale


class TestBuildTrajectory:
    """Tests for _build_trajectory — initial trajectory state from prediction snapshot."""

    def test_no_signals(self):
        traj = _build_trajectory(spec_level=0.5, design_signals=None, sigma=10)
        assert traj.convergence_rate == 0.0
        assert traj.estimated_remaining == 5  # round(10 * 0.5)
        assert traj.delta_k == []
        assert traj.transition_index is None

    def test_high_signal_density(self):
        signals = TestDesignSignals(
            boundary_points=5, equivalence_partitions=3,
            decision_rule_count=2, predicate_effect_links=0,
        )
        traj = _build_trajectory(spec_level=0.0, design_signals=signals, sigma=10)
        # signal_density = 10/10 = 1.0
        assert traj.convergence_rate == 1.0
        assert traj.estimated_remaining == 10

    def test_low_signal_density(self):
        signals = TestDesignSignals(
            boundary_points=1, equivalence_partitions=0,
            decision_rule_count=0, predicate_effect_links=0,
        )
        traj = _build_trajectory(spec_level=0.8, design_signals=signals, sigma=20)
        # signal_density = 1/20 = 0.05
        assert traj.convergence_rate == 0.05
        assert traj.estimated_remaining == 4  # round(20 * 0.2)

    def test_zero_sigma(self):
        signals = TestDesignSignals(boundary_points=5)
        traj = _build_trajectory(spec_level=0.0, design_signals=signals, sigma=0)
        assert traj.convergence_rate == 0.0
        assert traj.estimated_remaining == 0

    def test_fully_specified(self):
        traj = _build_trajectory(spec_level=1.0, design_signals=None, sigma=10)
        assert traj.estimated_remaining == 0


class TestUpdateTrajectory:
    """Tests for cross-run ΔK accumulation (Thm 3.4)."""

    def test_appends_delta(self):
        prev = TrajectoryState(delta_k=[0.2, 0.15], convergence_rate=0.175)
        updated = update_trajectory(prev, new_spec_level=0.6, previous_spec_level=0.5, sigma=10)
        assert len(updated.delta_k) == 3
        assert updated.delta_k[2] == pytest.approx(0.1)
        assert updated.estimated_remaining == 4  # round(10 * 0.4)

    def test_empty_trajectory_first_run(self):
        prev = TrajectoryState()
        updated = update_trajectory(prev, new_spec_level=0.3, previous_spec_level=0.0, sigma=10)
        assert updated.delta_k == [0.3]
        assert updated.convergence_rate == 0.3
        assert updated.transition_index is None

    def test_transition_detected(self):
        # 3 consecutive deltas below threshold (0.02)
        prev = TrajectoryState(delta_k=[0.2, 0.1, 0.01, 0.005])
        updated = update_trajectory(prev, new_spec_level=0.95, previous_spec_level=0.94, sigma=20)
        assert len(updated.delta_k) == 5
        assert updated.delta_k[-1] == pytest.approx(0.01)
        assert updated.transition_index is not None

    def test_no_transition_if_deltas_large(self):
        prev = TrajectoryState(delta_k=[0.2, 0.15])
        updated = update_trajectory(prev, new_spec_level=0.5, previous_spec_level=0.3, sigma=10)
        assert updated.transition_index is None

    def test_convergence_rate_uses_recent_window(self):
        prev = TrajectoryState(delta_k=[0.5, 0.4, 0.3, 0.2, 0.1])
        updated = update_trajectory(prev, new_spec_level=0.95, previous_spec_level=0.9, sigma=20)
        # Window is last 5: [0.3, 0.2, 0.1, 0.05] — wait, delta_k has 6 elements now
        # [0.5, 0.4, 0.3, 0.2, 0.1, 0.05], window = [0.2, 0.1, 0.05] — no, last 5
        # window = [0.4, 0.3, 0.2, 0.1, 0.05], mean = 0.21
        assert updated.convergence_rate > 0


class TestDetectTransitionPoint:
    """Tests for _detect_transition_point."""

    def test_no_transition_short_list(self):
        assert _detect_transition_point([0.01, 0.005]) is None

    def test_transition_at_start(self):
        result = _detect_transition_point([0.01, 0.005, 0.003])
        assert result == 0

    def test_transition_after_bulk(self):
        result = _detect_transition_point([0.5, 0.3, 0.01, 0.005, 0.003])
        assert result == 2

    def test_no_transition_all_large(self):
        assert _detect_transition_point([0.5, 0.3, 0.2, 0.1, 0.05]) is None

    def test_interrupted_run_resets(self):
        # Small, small, large, small, small, small
        result = _detect_transition_point([0.01, 0.005, 0.3, 0.01, 0.005, 0.003])
        assert result == 3


class TestDetectPhaseFromTrajectory:
    """Tests for ΔK-driven phase detection."""

    def test_complete_phase(self):
        traj = TrajectoryState(delta_k=[0.1])
        assert detect_phase_from_trajectory(traj, spec_level=0.96) == "complete"

    def test_fallback_with_insufficient_data(self):
        traj = TrajectoryState(delta_k=[0.1])
        assert detect_phase_from_trajectory(traj, spec_level=0.1) == "bulk"
        assert detect_phase_from_trajectory(traj, spec_level=0.5) == "transition"
        assert detect_phase_from_trajectory(traj, spec_level=0.8) == "tail"

    def test_transition_detected_means_tail(self):
        traj = TrajectoryState(delta_k=[0.2, 0.01, 0.005, 0.003], transition_index=1)
        assert detect_phase_from_trajectory(traj, spec_level=0.5) == "tail"

    def test_high_convergence_rate_means_bulk(self):
        traj = TrajectoryState(delta_k=[0.2, 0.15], convergence_rate=0.175)
        assert detect_phase_from_trajectory(traj, spec_level=0.3) == "bulk"

    def test_low_convergence_rate_means_tail(self):
        traj = TrajectoryState(delta_k=[0.02, 0.01], convergence_rate=0.015)
        assert detect_phase_from_trajectory(traj, spec_level=0.7) == "tail"

    def test_medium_convergence_rate_means_transition(self):
        traj = TrajectoryState(delta_k=[0.05, 0.04], convergence_rate=0.045)
        assert detect_phase_from_trajectory(traj, spec_level=0.5) == "transition"
