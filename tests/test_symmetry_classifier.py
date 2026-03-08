"""Tests for lintgate.specification.symmetry_classifier — Theorem 4.1."""

from __future__ import annotations

from lintgate.specification.mutation_engine import (
    CategoryResult,
    MutationCategory,
    ProfilingResult,
)
from lintgate.specification.symmetry_classifier import (
    classify_regime_from_mutations,
)


def _make_profiling(
    kill_matrix: dict[str, list[str]],
    total: int = 10,
    killed: int = 10,
    func_key: str = "test.py::f",
) -> ProfilingResult:
    return ProfilingResult(
        function_key=func_key,
        total_mutants=total,
        total_killed=killed,
        total_survived=total - killed,
        survival_rate=(total - killed) / total if total > 0 else 0.0,
        per_category=[
            CategoryResult(category=MutationCategory.VALUE, total=total, killed=killed)
        ],
        kill_matrix=kill_matrix,
    )


class TestClassifyRegimeFromMutations:
    def test_pure_always_a(self):
        profile = _make_profiling(kill_matrix={"VALUE_0: m": ["test_a"]})
        result = classify_regime_from_mutations(profile, sigma=30, is_pure=True)
        assert result.regime == "A"
        assert "pure" in result.regime_rationale
        assert result.data_source == "mutation"

    def test_high_symmetry_is_a(self):
        """All mutations killed by same test set → high symmetry."""
        profile = _make_profiling(
            kill_matrix={
                "VALUE_0: m0": ["test_a", "test_b"],
                "VALUE_1: m1": ["test_a", "test_b"],
                "VALUE_2: m2": ["test_a", "test_b"],
            }
        )
        # All have identical kill sets → 1 equivalence class → high symmetry
        result = classify_regime_from_mutations(profile, sigma=15, is_pure=False, parameter_count=3)
        assert result.symmetry_group_size == 1
        # With only 1 equivalence class and 3! max, ratio is low
        # But category independence will be low too (only VALUE category)
        assert result.regime == "A"

    def test_independent_categories_push_toward_b(self):
        """Each category killed by unique tests → high independence."""
        profile = _make_profiling(
            kill_matrix={
                "VALUE_0: m0": ["test_value"],
                "BOUNDARY_0: m1": ["test_boundary"],
                "SWAP_0: m2": ["test_swap"],
            }
        )
        result = classify_regime_from_mutations(
            profile, sigma=15, is_pure=False, parameter_count=3
        )
        assert result.category_independence > 0.5

    def test_fallback_when_no_profiling(self):
        result = classify_regime_from_mutations(None, sigma=10, is_pure=False)
        assert result.data_source == "symbolic"
        assert result.regime == "A"

    def test_fallback_when_empty_profiling(self):
        profile = ProfilingResult(total_mutants=0)
        result = classify_regime_from_mutations(profile, sigma=10, is_pure=False)
        assert result.data_source == "symbolic"

    def test_to_dict(self):
        profile = _make_profiling(kill_matrix={"VALUE_0: m": ["test_a"]})
        result = classify_regime_from_mutations(profile, sigma=5, is_pure=False)
        d = result.to_dict()
        assert "regime" in d
        assert "data_source" in d
        assert "symmetry_ratio" in d
        assert "category_independence" in d
