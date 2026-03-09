"""Tests for lintgate.specification.greedy_convergence — Theorem 3.2 verification."""

from __future__ import annotations

from lintgate.specification.greedy_convergence import analyze_convergence
from lintgate.specification.mutation_engine import (
    CategoryResult,
    MutationCategory,
    ProfilingResult,
)


def _make_profiling(
    total: int,
    killed: int,
    kill_matrix: dict[str, list[str]],
    func_key: str = "test.py::f",
) -> ProfilingResult:
    return ProfilingResult(
        function_key=func_key,
        total_mutants=total,
        total_killed=killed,
        total_survived=total - killed,
        survival_rate=(total - killed) / total if total > 0 else 0.0,
        per_category=[CategoryResult(category=MutationCategory.VALUE, total=total, killed=killed)],
        kill_matrix=kill_matrix,
    )


class TestAnalyzeConvergence:
    def test_three_mutants_three_tests_optimal(self):
        """Each test kills exactly one unique mutant → efficiency ≈ 1.0."""
        profile = _make_profiling(
            total=3,
            killed=3,
            kill_matrix={
                "VALUE_0: m0": ["test_a"],
                "VALUE_1: m1": ["test_b"],
                "VALUE_2: m2": ["test_c"],
            },
        )
        result = analyze_convergence(profile, sigma=3)
        assert result.is_fully_specified
        assert len(result.redundant_tests) == 0
        assert result.convergence_efficiency == 1.0

    def test_redundant_tests_detected(self):
        """Two tests kill the same mutant → one is redundant."""
        profile = _make_profiling(
            total=2,
            killed=2,
            kill_matrix={
                "VALUE_0: m0": ["test_a", "test_b"],
                "VALUE_1: m1": ["test_a"],
            },
        )
        result = analyze_convergence(profile, sigma=2)
        # Greedy ordering: test_a kills 2 mutants (m0, m1), test_b kills 1 (m0).
        # After test_a runs first, test_b kills nothing new → test_b is redundant.
        assert result.redundant_tests == ["test_b"]

    def test_zero_sigma_trivially_specified(self):
        profile = _make_profiling(total=0, killed=0, kill_matrix={})
        result = analyze_convergence(profile, sigma=0)
        assert result.is_fully_specified
        assert not result.is_error_state

    def test_zero_sigma_with_mutants_is_error_state(self):
        """sigma=0 but mutants exist → error state, NOT fully specified."""
        profile = _make_profiling(total=3, killed=1, kill_matrix={"VALUE_0: m0": ["test_a"]})
        result = analyze_convergence(profile, sigma=0)
        assert not result.is_fully_specified
        assert result.is_error_state
        assert "sigma=0" in result.error_reason
        assert "total_mutants=3" in result.error_reason

    def test_negative_sigma_with_mutants_is_error_state(self):
        """Negative sigma with mutants → error state, NOT fully specified."""
        profile = _make_profiling(total=2, killed=0, kill_matrix={})
        result = analyze_convergence(profile, sigma=-1)
        assert not result.is_fully_specified
        assert result.is_error_state
        assert "sigma=-1" in result.error_reason

    def test_no_tests_no_convergence(self):
        profile = _make_profiling(total=3, killed=0, kill_matrix={})
        result = analyze_convergence(profile, sigma=3)
        assert not result.is_fully_specified
        assert len(result.steps) == 0

    def test_custom_ordering(self):
        """Custom ordering affects which tests appear redundant."""
        profile = _make_profiling(
            total=2,
            killed=2,
            kill_matrix={
                "VALUE_0: m0": ["test_a"],
                "VALUE_1: m1": ["test_b"],
            },
        )
        result = analyze_convergence(profile, sigma=2, test_ordering=["test_b", "test_a"])
        assert result.is_fully_specified
        # With this ordering, test_b goes first
        assert result.steps[0].test_name == "test_b"

    def test_to_dict(self):
        profile = _make_profiling(total=1, killed=1, kill_matrix={"VALUE_0: m0": ["test_a"]})
        result = analyze_convergence(profile, sigma=1)
        d = result.to_dict()
        assert "steps" in d
        assert "redundant_tests" in d
        assert "convergence_efficiency" in d
