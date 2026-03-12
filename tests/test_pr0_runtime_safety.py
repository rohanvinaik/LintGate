"""PR0: Runtime safety and budget semantics tests.

Validates that mutation tools are bounded, predictable, and return
partial results instead of hanging.
"""

from __future__ import annotations

import ast

from lintgate.specification.mutation_engine import (
    MutationCategory,
    ProfilingResult,
    run_function_profiling,
    run_function_sampling,
)


def _parse_func(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            return node
    msg = "No function found"
    raise ValueError(msg)


# ── Budget separation tests ──────────────────────────────────────


class TestSamplingBudgetSeparation:
    """Verify that sampling separates outer budget from per-mutant timeout."""

    def test_per_mutant_timeout_is_independent_of_budget(self):
        """per_mutant_timeout_ms should be used for evaluate_mutant, not budget_ms."""
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        def test_fn(mutated_func):
            assert mutated_func(0) == 1

        # Large budget, small per-mutant timeout — should work fine
        result = run_function_sampling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [test_fn],
            original,
            budget_ms=10000,
            per_mutant_timeout_ms=500,
        )
        assert result.total_mutants >= 1
        assert not result.budget_exhausted

    def test_budget_exhaustion_stops_early(self):
        """A very tight budget should cause early termination."""
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        original = lambda x: x + 1 + 2 + 3  # noqa: E731

        # Extremely tight budget — 0ms means immediate exhaustion
        result = run_function_sampling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
            budget_ms=0,
            per_mutant_timeout_ms=500,
        )
        assert result.budget_exhausted

    def test_default_per_mutant_timeout(self):
        """Default per_mutant_timeout_ms should be 500."""
        import inspect

        sig = inspect.signature(run_function_sampling)
        assert sig.parameters["per_mutant_timeout_ms"].default == 500


class TestProfilingBudget:
    """Verify that profiling respects optional budget."""

    def test_profiling_no_budget_runs_all(self):
        """Without budget_ms, profiling should evaluate all mutants."""
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        original = lambda x: x + 1 + 2 + 3  # noqa: E731

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
        )
        assert not result.budget_exhausted
        # Should have processed all VALUE mutants (3 constants)
        assert result.total_mutants == 3

    def test_profiling_with_budget_returns_partial(self):
        """Profiling with tight budget should return partial + budget_exhausted."""
        func = _parse_func("def f(x): return x + 1 + 2 + 3")
        original = lambda x: x + 1 + 2 + 3  # noqa: E731

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
            budget_ms=0,  # Immediate exhaustion
        )
        assert result.budget_exhausted

    def test_profiling_budget_none_is_unlimited(self):
        """budget_ms=None should mean unlimited (backward compat)."""
        func = _parse_func("def f(x): return x + 1")
        original = lambda x: x + 1  # noqa: E731

        result = run_function_profiling(
            func,
            "test.py::f",
            {MutationCategory.VALUE},
            [],
            original,
            budget_ms=None,
        )
        assert not result.budget_exhausted

    def test_budget_exhausted_in_to_dict(self):
        """budget_exhausted should appear in to_dict output."""
        result = ProfilingResult(budget_exhausted=True)
        d = result.to_dict()
        assert d["budget_exhausted"] is True

    def test_budget_exhausted_false_in_to_dict(self):
        result = ProfilingResult(budget_exhausted=False)
        d = result.to_dict()
        assert d["budget_exhausted"] is False
