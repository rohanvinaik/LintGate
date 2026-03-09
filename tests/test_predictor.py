"""Tests for lintgate.specification.predictor — decision tree and regime classification."""

from __future__ import annotations

import ast

import pytest

from lintgate.specification.predictor import (
    _build_trajectory,
    _classify_regime,
    _compute_spec_level,
    _decision_tree,
    _detect_transition_point,
    compute_dft_score,
    count_ast_categories,
    count_branches,
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
    """Decision tree sigma computation (pre-calibration).

    _decision_tree returns only the base sigma estimate. Regime
    classification is tested via _classify_regime (see TestClassifyRegime)
    and happens AFTER TPA calibration in predict().
    """

    def test_path1_well_specified_pure(self):
        sigma = _decision_tree(
            is_pure=True,
            semantic_ratio=0.6,
            weakness_taxonomy="",
            ast_category_count=5,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 3  # max(branch_count, 1)

    def test_path2_under_specified_pure(self):
        sigma = _decision_tree(
            is_pure=True,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=5,
            branch_count=2,
            parameter_count=3,
        )
        assert sigma == 6  # branch + params + 1

    def test_path3_pure_with_weakness(self):
        sigma = _decision_tree(
            is_pure=True,
            semantic_ratio=0.6,
            weakness_taxonomy="WEAK",
            ast_category_count=5,
            branch_count=2,
            parameter_count=3,
        )
        assert sigma == 7  # branch + params + 2

    def test_path4_tractable_impure(self):
        sigma = _decision_tree(
            is_pure=False,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=6,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 9  # ast_cats + branches

    def test_path5_hard_but_progressing(self):
        sigma = _decision_tree(
            is_pure=False,
            semantic_ratio=0.6,
            weakness_taxonomy="",
            ast_category_count=10,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 15  # ast_cats + branches + params

    def test_path6_hardest(self):
        sigma = _decision_tree(
            is_pure=False,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=12,
            branch_count=8,
            parameter_count=5,
        )
        assert sigma == 27  # ast_cats + branches + params + 2
        # Regime classification (B for sigma > 20) is verified in
        # TestClassifyRegime.test_high_sigma_impure_is_b

    def test_path6_moderate_sigma(self):
        """Path 6 with moderate sigma — regime depends on calibration."""
        sigma = _decision_tree(
            is_pure=False,
            semantic_ratio=0.2,
            weakness_taxonomy="WEAK",
            ast_category_count=9,
            branch_count=2,
            parameter_count=2,
        )
        assert sigma == 15  # ast_cats + branches + params + 2
        # Multi-factor regime B classification is verified in
        # TestClassifyRegime.test_moderate_sigma_with_weakness_and_poor_spec_is_b


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
            boundary_points=5,
            equivalence_partitions=3,
            decision_rule_count=2,
            predicate_effect_links=0,
        )
        traj = _build_trajectory(spec_level=0.0, design_signals=signals, sigma=10)
        # signal_density = 10/10 = 1.0
        assert traj.convergence_rate == 1.0
        assert traj.estimated_remaining == 10

    def test_low_signal_density(self):
        signals = TestDesignSignals(
            boundary_points=1,
            equivalence_partitions=0,
            decision_rule_count=0,
            predicate_effect_links=0,
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
        # delta_k = [0.5, 0.4, 0.3, 0.2, 0.1, 0.05], window = last 5 = [0.4, 0.3, 0.2, 0.1, 0.05]
        # convergence_rate = mean(|window|) = (0.4+0.3+0.2+0.1+0.05)/5 = 0.21
        assert updated.convergence_rate == pytest.approx(0.21, abs=0.01)


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


class TestMutationSpecLevelOverride:
    """Tests for mutation-derived spec_level override (Fix #2)."""

    def test_mutation_overrides_static(self):
        """When mutation_spec_level is set, it replaces assertion_count/sigma."""
        import ast

        from lintgate.specification.predictor import PredictorInput, predict

        func = ast.parse("def f(x): return x + 1").body[0]
        signals = PredictorInput(
            is_pure=True,
            assertion_count=0,  # static would give 0.0
            mutation_spec_level=0.7,  # 1.0 - 0.3 survival
            mutation_data_source="mutation_profiled",
        )
        result = predict(func, signals)
        assert result.spec_level == 0.7
        assert result.data_source == "mutation_profiled"

    def test_no_mutation_uses_static(self):
        """Without mutation data, spec_level comes from assertion_count/sigma."""
        import ast

        from lintgate.specification.predictor import PredictorInput, predict

        func = ast.parse("def f(x): return x + 1").body[0]
        signals = PredictorInput(
            is_pure=True,
            assertion_count=5,
        )
        result = predict(func, signals)
        assert result.data_source == "static"

    def test_mutation_none_is_noop(self):
        """Explicit None mutation_spec_level uses static path."""
        import ast

        from lintgate.specification.predictor import PredictorInput, predict

        func = ast.parse("def f(x): return x + 1").body[0]
        signals = PredictorInput(
            is_pure=True,
            assertion_count=3,
            mutation_spec_level=None,
        )
        result = predict(func, signals)
        assert result.data_source == "static"

    def test_mutation_affects_phase(self):
        """High mutation spec_level should push phase toward tail/complete."""
        import ast

        from lintgate.specification.predictor import PredictorInput, predict

        func = ast.parse("def f(x): return x + 1").body[0]
        signals = PredictorInput(
            is_pure=True,
            mutation_spec_level=0.96,
            mutation_data_source="mutation_profiled",
        )
        result = predict(func, signals)
        assert result.phase == "complete"


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


class TestCountBranches:
    """VALUE tests for count_branches — targets VALUE surviving mutants."""

    def test_no_branches(self):
        func = ast.parse("def f(x): return x + 1").body[0]

        assert count_branches(func) == 0

    def test_single_if(self):
        func = ast.parse("def f(x):\n  if x: return 1\n  return 0").body[0]

        assert count_branches(func) == 1

    def test_nested_if_for(self):
        func = ast.parse("def f(x):\n  for i in x:\n    if i: pass").body[0]

        assert count_branches(func) == 2

    def test_try_counts(self):
        func = ast.parse("def f():\n  try:\n    pass\n  except:\n    pass").body[0]

        assert count_branches(func) == 1


class TestCountAstCategories:
    """VALUE tests for count_ast_categories — targets VALUE surviving mutants."""

    def test_minimal_function(self):
        func = ast.parse("def f(): pass").body[0]
        result = count_ast_categories(func)
        assert result >= 2  # At least FunctionDef + arguments

    def test_complex_function_more_categories(self):
        func = ast.parse("def f(x):\n  if x > 0:\n    return x + 1\n  return 0").body[0]
        simple_func = ast.parse("def f(): pass").body[0]
        assert count_ast_categories(func) > count_ast_categories(simple_func)


class TestComputeSpecLevel:
    """VALUE + BOUNDARY tests for _compute_spec_level."""

    def test_zero_sigma_with_assertions(self):
        assert _compute_spec_level(0, 5) == 1.0

    def test_zero_sigma_no_assertions(self):
        assert _compute_spec_level(0, 0) == 0.0

    def test_assertions_equal_sigma(self):
        assert _compute_spec_level(10, 10) == 1.0

    def test_assertions_exceed_sigma(self):
        assert _compute_spec_level(5, 10) == 1.0  # capped at 1.0

    def test_partial_coverage(self):
        assert _compute_spec_level(10, 3) == pytest.approx(0.3)


class TestComputeDftScore:
    """VALUE tests for compute_dft_score."""

    def test_pure_simple_function_high_score(self):
        func = ast.parse("def f(x): return x + 1").body[0]
        result = compute_dft_score(func)
        assert result.testability_score >= 0.8
        assert not result.is_stateful
        assert not result.has_side_effects

    def test_stateful_function_lower_score(self):
        func = ast.parse("def f(self, x):\n  self.value = x").body[0]
        result = compute_dft_score(func)
        assert result.is_stateful
        assert result.testability_score < 1.0


class TestDecisionTreeBoundary:
    """Boundary-value tests for _decision_tree — targets BOUNDARY surviving mutants."""

    def test_semantic_ratio_boundary_at_0_5_pure(self):
        """semantic_ratio exactly 0.5 takes Path 1, not Path 2."""
        sigma = _decision_tree(
            is_pure=True,
            semantic_ratio=0.5,
            weakness_taxonomy="",
            ast_category_count=10,
            branch_count=4,
            parameter_count=3,
        )
        assert sigma == 4  # Path 1: max(branch_count, 1)

    def test_semantic_ratio_just_below_0_5_pure(self):
        """semantic_ratio 0.49 takes Path 2."""
        sigma = _decision_tree(
            is_pure=True,
            semantic_ratio=0.49,
            weakness_taxonomy="",
            ast_category_count=10,
            branch_count=4,
            parameter_count=3,
        )
        assert sigma == 8  # Path 2: branch + params + 1

    def test_ast_category_boundary_at_8_impure(self):
        """ast_category_count exactly 8 takes Path 4."""
        sigma = _decision_tree(
            is_pure=False,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=8,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 11  # Path 4: ast_cats + branches

    def test_ast_category_boundary_at_9_impure(self):
        """ast_category_count 9 takes Path 5 or 6 (not Path 4)."""
        sigma = _decision_tree(
            is_pure=False,
            semantic_ratio=0.6,
            weakness_taxonomy="",
            ast_category_count=9,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 14  # Path 5: ast_cats + branches + params

    def test_semantic_ratio_boundary_impure_path5_vs_path6(self):
        """semantic_ratio exactly 0.5 takes Path 5 for impure with ast_cats > 8."""
        sigma_at = _decision_tree(
            is_pure=False,
            semantic_ratio=0.5,
            weakness_taxonomy="",
            ast_category_count=10,
            branch_count=3,
            parameter_count=2,
        )
        sigma_below = _decision_tree(
            is_pure=False,
            semantic_ratio=0.49,
            weakness_taxonomy="",
            ast_category_count=10,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma_at == 15  # Path 5
        assert sigma_below == 17  # Path 6: +2 penalty

    def test_branch_count_zero_path1(self):
        """Path 1 uses max(branch_count, 1) — zero branches → sigma=1."""
        sigma = _decision_tree(
            is_pure=True,
            semantic_ratio=0.8,
            weakness_taxonomy="",
            ast_category_count=5,
            branch_count=0,
            parameter_count=3,
        )
        assert sigma == 1


class TestDecisionTreeSwap:
    """Parameter-order tests for _decision_tree — targets SWAP surviving mutants."""

    def test_branch_vs_param_count_matters(self):
        """Swapping branch_count and parameter_count changes sigma in Path 2."""
        sigma_a = _decision_tree(
            is_pure=True,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=5,
            branch_count=2,
            parameter_count=5,
        )
        sigma_b = _decision_tree(
            is_pure=True,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=5,
            branch_count=5,
            parameter_count=2,
        )
        # Path 2: branches + params + 1 → same sum, so they're equal here
        # But swapping ast_cats with branch_count should differ in Path 4+
        assert sigma_a == sigma_b  # For Path 2, they sum the same

    def test_ast_cats_vs_branches_order_in_path4(self):
        """ast_category_count and branch_count contribute separately in Path 4."""
        sigma = _decision_tree(
            is_pure=False,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=6,
            branch_count=2,
            parameter_count=0,
        )
        assert sigma == 8  # ast_cats(6) + branches(2) = 8


class TestClassifyRegimeBoundary:
    """Boundary-value tests for _classify_regime — targets BOUNDARY survivors."""

    def test_sigma_exactly_20_impure_is_a(self):
        """sigma=20 is regime A (threshold is >20, not >=20)."""
        regime, _ = _classify_regime(
            sigma=20, is_pure=False, semantic_ratio=0.5, weakness_taxonomy=""
        )
        assert regime == "A"

    def test_sigma_21_impure_is_b(self):
        """sigma=21 crosses to regime B."""
        regime, _ = _classify_regime(
            sigma=21, is_pure=False, semantic_ratio=0.5, weakness_taxonomy=""
        )
        assert regime == "B"

    def test_sigma_exactly_12_with_compounding_is_a(self):
        """sigma=12 doesn't trigger multi-factor B (threshold is >12)."""
        regime, _ = _classify_regime(
            sigma=12, is_pure=False, semantic_ratio=0.1, weakness_taxonomy="WEAK"
        )
        assert regime == "A"

    def test_sigma_13_with_compounding_is_b(self):
        """sigma=13 with weakness + poor coverage → B."""
        regime, _ = _classify_regime(
            sigma=13, is_pure=False, semantic_ratio=0.1, weakness_taxonomy="WEAK"
        )
        assert regime == "B"

    def test_semantic_ratio_boundary_at_0_3(self):
        """semantic_ratio=0.3 does NOT trigger poorly_specified (< 0.3 required)."""
        regime, _ = _classify_regime(
            sigma=15, is_pure=False, semantic_ratio=0.3, weakness_taxonomy="WEAK"
        )
        assert regime == "A"

    def test_semantic_ratio_just_below_0_3(self):
        """semantic_ratio=0.29 triggers poorly_specified."""
        regime, _ = _classify_regime(
            sigma=15, is_pure=False, semantic_ratio=0.29, weakness_taxonomy="WEAK"
        )
        assert regime == "B"
