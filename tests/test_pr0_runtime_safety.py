"""PR0: Runtime safety and budget semantics tests.

Validates that mutation tools are bounded, predictable, and return
partial results instead of hanging.
"""

from __future__ import annotations

import ast
import time
from unittest.mock import MagicMock, patch

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


# ── Fallback test cap tests ──────────────────────────────────────


class TestFallbackTestCap:
    """Verify fallback test loading is capped."""

    def test_fallback_cap_applied_field_exists(self):
        from mcp_tools._mutation_impl import DiscoveryDiagnostics

        diag = DiscoveryDiagnostics()
        assert hasattr(diag, "fallback_cap_applied")
        assert diag.fallback_cap_applied is False

    def test_fallback_cap_in_to_dict(self):
        from mcp_tools._mutation_impl import DiscoveryDiagnostics

        diag = DiscoveryDiagnostics(fallback_used=True, fallback_cap_applied=True)
        d = diag.to_dict()
        assert d["fallback_cap_applied"] is True

    def test_load_test_callables_caps_fallback(self):
        """When fallback fires, cap at max_fallback_tests."""
        from mcp_tools._mutation_impl import load_test_callables

        # Mock the test impact map to return no refs (trigger fallback)
        # and _load_all_tests_from_files to return many tests
        fake_tests = [MagicMock(name=f"test_{i}") for i in range(100)]

        with (
            patch("lintgate.specification.test_impact.build_test_impact_map") as mock_impact,
            patch("mcp_tools._mutation_impl._load_all_tests_from_files", return_value=fake_tests),
        ):
            mock_impact.return_value.tests_for.return_value = []
            callables, diag = load_test_callables(
                ["fake_test.py"],
                "some_func",
                max_fallback_tests=10,
            )
            assert len(callables) == 10
            assert diag.fallback_used
            assert diag.fallback_cap_applied

    def test_load_test_callables_no_cap_when_under_limit(self):
        """No cap applied when fallback tests are under the limit."""
        from mcp_tools._mutation_impl import load_test_callables

        fake_tests = [MagicMock(name=f"test_{i}") for i in range(5)]

        with (
            patch("lintgate.specification.test_impact.build_test_impact_map") as mock_impact,
            patch("mcp_tools._mutation_impl._load_all_tests_from_files", return_value=fake_tests),
        ):
            mock_impact.return_value.tests_for.return_value = []
            callables, diag = load_test_callables(
                ["fake_test.py"],
                "some_func",
                max_fallback_tests=50,
            )
            assert len(callables) == 5
            assert diag.fallback_used
            assert not diag.fallback_cap_applied

    def test_default_max_fallback_tests(self):
        """Default cap should be 50."""
        import inspect

        from mcp_tools._mutation_impl import load_test_callables

        sig = inspect.signature(load_test_callables)
        assert sig.parameters["max_fallback_tests"].default == 50


# ── Validate targets / budget splitting tests ────────────────────


class TestValidateTargetsBudgetSplitting:
    """Verify impl_refactor_loop splits budget across functions."""

    def test_impl_refactor_loop_accepts_budget(self):
        """impl_refactor_loop should accept budget_ms parameter."""
        import inspect

        from mcp_tools._mutation_tools_impl import impl_refactor_loop

        sig = inspect.signature(impl_refactor_loop)
        assert "budget_ms" in sig.parameters
        assert sig.parameters["budget_ms"].default == 300_000

    def test_hard_timeout_constant(self):
        """Hard circuit breaker should be 10 minutes."""
        from mcp_tools._mutation_tools_impl import _HARD_TIMEOUT_MS

        assert _HARD_TIMEOUT_MS == 600_000

    def test_validate_targets_splits_budget(self):
        """Budget should be split evenly across target functions."""
        from mcp_tools._mutation_tools_impl import _validate_targets

        func1 = _parse_func("def f(x): return x + 1")
        func2 = _parse_func("def g(x): return x + 2")
        targets = [("f", func1), ("g", func2)]

        with (
            patch("mcp_tools._mutation_tools_impl.load_test_callables") as mock_load,
            patch("mcp_tools._mutation_tools_impl.detect_purity", return_value=False),
            patch("mcp_tools._mutation_tools_impl.save_cached_state"),
            patch("mcp_tools._mutation_tools_impl.load_cached_state", return_value=None),
        ):
            from mcp_tools._mutation_impl import DiscoveryDiagnostics

            mock_load.return_value = ([], DiscoveryDiagnostics())

            results, timed_out = _validate_targets(
                targets,
                "/fake/path.py",
                "path.py",
                MagicMock(),
                [],
                budget_ms=10000,
            )
            # Both functions should have been processed
            assert len(results) == 2
            assert len(timed_out) == 0
            # Each should have per_func_budget_ms in output
            for r in results:
                assert "per_func_budget_ms" in r

    def test_validate_targets_returns_partial_on_budget_exhaust(self):
        """When budget is exhausted, remaining functions go to timed_out list."""
        from mcp_tools._mutation_tools_impl import _validate_targets

        func1 = _parse_func("def f(x): return x + 1")
        func2 = _parse_func("def g(x): return x + 2")
        targets = [("f", func1), ("g", func2)]

        # Use budget=0 so the second function should be timed out
        # after the first one runs
        with (
            patch("mcp_tools._mutation_tools_impl.load_test_callables") as mock_load,
            patch("mcp_tools._mutation_tools_impl.detect_purity", return_value=False),
            patch("mcp_tools._mutation_tools_impl.save_cached_state"),
            patch("mcp_tools._mutation_tools_impl.load_cached_state", return_value=None),
        ):
            from mcp_tools._mutation_impl import DiscoveryDiagnostics

            mock_load.return_value = ([], DiscoveryDiagnostics())

            # Start time is in the past so budget is already exhausted
            past_start = time.monotonic() - 1.0  # 1 second ago

            results, timed_out = _validate_targets(
                targets,
                "/fake/path.py",
                "path.py",
                MagicMock(),
                [],
                budget_ms=0,
                call_start=past_start,
            )
            # Both functions should have been timed out
            assert len(timed_out) == 2


# ── MCP tool signature tests ─────────────────────────────────────


class TestMCPToolSignatures:
    """Verify MCP tool impl functions have correct budget parameters."""

    def test_impl_run_full_has_budget_params(self):
        """impl_run_full should accept budget_ms and per_mutant_timeout_ms."""
        import inspect

        from mcp_tools._mutation_tools_impl import impl_run_full

        sig = inspect.signature(impl_run_full)
        assert "budget_ms" in sig.parameters
        assert "per_mutant_timeout_ms" in sig.parameters
        assert sig.parameters["budget_ms"].default == 600_000
        assert sig.parameters["per_mutant_timeout_ms"].default == 5000

    def test_impl_refactor_loop_has_budget(self):
        """impl_refactor_loop should accept budget_ms."""
        import inspect

        from mcp_tools._mutation_tools_impl import impl_refactor_loop

        sig = inspect.signature(impl_refactor_loop)
        assert "budget_ms" in sig.parameters
        assert sig.parameters["budget_ms"].default == 300_000

    def test_mutation_tools_register_passes_budget_through(self):
        """MCP registration should wire budget params into tool calls."""
        # Verify the source code of register passes budget_ms to impl
        import inspect

        from mcp_tools import mutation_tools

        source = inspect.getsource(mutation_tools.register)
        # mutation_run_full passes budget_ms and per_mutant_timeout_ms
        assert "budget_ms" in source
        assert "per_mutant_timeout_ms" in source
        # mutation_validate_tests passes budget_ms
        assert "impl_refactor_loop(helpers, path, file, function, budget_ms)" in source


# ── impl_run_full budget tests ───────────────────────────────────


class TestImplRunFullBudget:
    """Verify impl_run_full passes budget through to profiling."""

    def test_impl_run_full_has_budget_params(self):
        import inspect

        from mcp_tools._mutation_tools_impl import impl_run_full

        sig = inspect.signature(impl_run_full)
        assert "budget_ms" in sig.parameters
        assert "per_mutant_timeout_ms" in sig.parameters
        assert sig.parameters["budget_ms"].default == 600_000
        assert sig.parameters["per_mutant_timeout_ms"].default == 5000
