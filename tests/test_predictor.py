"""Tests for lintgate.specification.predictor — decision tree and regime classification."""

from __future__ import annotations

from lintgate.specification.predictor import _classify_regime, _decision_tree


class TestClassifyRegime:
    """Multi-factor regime classification tests."""

    def test_pure_always_a(self):
        """Pure functions are always regime A regardless of sigma."""
        assert (
            _classify_regime(sigma=30, is_pure=True, semantic_ratio=0.0, weakness_taxonomy="WEAK")
            == "A"
        )

    def test_high_sigma_impure_is_b(self):
        """Impure functions with sigma > 20 → regime B."""
        assert (
            _classify_regime(sigma=21, is_pure=False, semantic_ratio=0.5, weakness_taxonomy="")
            == "B"
        )

    def test_moderate_sigma_with_weakness_and_poor_spec_is_b(self):
        """Impure, sigma > 12, known weakness, poor coverage → regime B."""
        assert (
            _classify_regime(sigma=13, is_pure=False, semantic_ratio=0.2, weakness_taxonomy="WEAK")
            == "B"
        )

    def test_moderate_sigma_without_weakness_is_a(self):
        """Moderate sigma without weakness → regime A."""
        assert (
            _classify_regime(sigma=15, is_pure=False, semantic_ratio=0.2, weakness_taxonomy="")
            == "A"
        )

    def test_moderate_sigma_with_weakness_but_good_spec_is_a(self):
        """Moderate sigma with weakness but good coverage → regime A."""
        assert (
            _classify_regime(sigma=15, is_pure=False, semantic_ratio=0.5, weakness_taxonomy="WEAK")
            == "A"
        )

    def test_low_sigma_always_a(self):
        """Low sigma is always A even with bad factors."""
        assert (
            _classify_regime(sigma=10, is_pure=False, semantic_ratio=0.1, weakness_taxonomy="WEAK")
            == "A"
        )

    def test_boundary_sigma_20_is_a(self):
        """Sigma exactly 20 is still A (threshold is > 20)."""
        assert (
            _classify_regime(sigma=20, is_pure=False, semantic_ratio=0.5, weakness_taxonomy="")
            == "A"
        )

    def test_boundary_sigma_12_not_b(self):
        """Sigma exactly 12 doesn't trigger multi-factor B (threshold is > 12)."""
        assert (
            _classify_regime(sigma=12, is_pure=False, semantic_ratio=0.1, weakness_taxonomy="WEAK")
            == "A"
        )

    def test_healthy_weakness_not_counted(self):
        """HEALTHY weakness taxonomy is treated same as empty."""
        assert (
            _classify_regime(
                sigma=15, is_pure=False, semantic_ratio=0.1, weakness_taxonomy="HEALTHY"
            )
            == "A"
        )


class TestDecisionTree:
    """Decision tree sigma computation + regime integration."""

    def test_path1_well_specified_pure(self):
        sigma, regime = _decision_tree(
            is_pure=True,
            semantic_ratio=0.6,
            weakness_taxonomy="",
            ast_category_count=5,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 3  # max(branch_count, 1)
        assert regime == "A"

    def test_path2_under_specified_pure(self):
        sigma, regime = _decision_tree(
            is_pure=True,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=5,
            branch_count=2,
            parameter_count=3,
        )
        assert sigma == 6  # branch + params + 1
        assert regime == "A"

    def test_path3_pure_with_weakness(self):
        sigma, regime = _decision_tree(
            is_pure=True,
            semantic_ratio=0.6,
            weakness_taxonomy="WEAK",
            ast_category_count=5,
            branch_count=2,
            parameter_count=3,
        )
        assert sigma == 7  # branch + params + 2
        assert regime == "A"

    def test_path4_tractable_impure(self):
        sigma, regime = _decision_tree(
            is_pure=False,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=6,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 9  # ast_cats + branches
        assert regime == "A"

    def test_path5_hard_but_progressing(self):
        sigma, regime = _decision_tree(
            is_pure=False,
            semantic_ratio=0.6,
            weakness_taxonomy="",
            ast_category_count=10,
            branch_count=3,
            parameter_count=2,
        )
        assert sigma == 15  # ast_cats + branches + params
        assert regime == "A"

    def test_path6_hardest_regime_b(self):
        sigma, regime = _decision_tree(
            is_pure=False,
            semantic_ratio=0.3,
            weakness_taxonomy="",
            ast_category_count=12,
            branch_count=8,
            parameter_count=5,
        )
        assert sigma == 27  # ast_cats + branches + params + 2
        assert regime == "B"  # sigma > 20

    def test_path6_multi_factor_regime_b(self):
        """Path 6 with moderate sigma but weakness + poor spec → B."""
        sigma, regime = _decision_tree(
            is_pure=False,
            semantic_ratio=0.2,
            weakness_taxonomy="WEAK",
            ast_category_count=9,
            branch_count=2,
            parameter_count=2,
        )
        assert sigma == 15  # ast_cats + branches + params + 2
        assert regime == "B"  # sigma > 12, weakness, semantic < 0.3
