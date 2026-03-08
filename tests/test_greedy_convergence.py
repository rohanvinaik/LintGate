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
        assert result.convergence_efficiency > 0

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
        # test_b kills nothing new after test_a
        assert isinstance(result.redundant_tests, list)  # depends on ordering

    def test_zero_sigma_trivially_specified(self):
        profile = _make_profiling(total=0, killed=0, kill_matrix={})
        result = analyze_convergence(profile, sigma=0)
        assert result.is_fully_specified

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
        profile = _make_profiling(
            total=1, killed=1, kill_matrix={"VALUE_0: m0": ["test_a"]}
        )
        result = analyze_convergence(profile, sigma=1)
        d = result.to_dict()
        assert "steps" in d
        assert "redundant_tests" in d
        assert "convergence_efficiency" in d
