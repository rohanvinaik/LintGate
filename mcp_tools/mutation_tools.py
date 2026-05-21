"""Mutation tools — AST mutation engine MCP surface.

Provides mutation_run_sampling, mutation_run_full, mutation_get_state,
mutation_prescribe, mutation_decompose, mutation_refactor_loop,
mutation_prescribe_tests, mutation_validate_tests, mutation_clear_state.

Implementation functions live in _mutation_tools_impl.py.
"""

from __future__ import annotations

from typing import Any

from ._mutation_tools_impl import (
    impl_clear_state,
    impl_decompose,
    impl_get_state,
    impl_prescribe,
    impl_prescribe_tests,
    impl_refactor_loop,
    impl_run_full,
    impl_run_sampling,
    impl_validate_tests,
)

# Backward-compatible aliases for test imports.
_impl_run_sampling = impl_run_sampling
_impl_run_full = impl_run_full
_impl_get_state = impl_get_state
_impl_prescribe = impl_prescribe
_impl_decompose = impl_decompose
_impl_refactor_loop = impl_refactor_loop
_impl_validate_tests = impl_validate_tests
_impl_prescribe_tests = impl_prescribe_tests
_impl_clear_state = impl_clear_state

# ── MCP Registration ──────────────────────────────────────────────


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register mutation analysis tools on the shared MCP instance."""

    @mcp.tool()
    def mutation_run_sampling(
        path: str, file: str, function: str | None = None, budget_ms: float = 500
    ) -> str:
        """Fast sampled mutation run — inline AST mutation sampling.

        WHEN TO USE: After editing specific files. Generates ≤3 mutants per
        semantic category (VALUE, SWAP, STATE, BOUNDARY, TYPE), evaluates
        within time budget. Returns per-category kill/survive counts.

        Args:
            path: Project root path.
            file: Relative path to the Python file.
            function: Optional specific function name.
            budget_ms: Time budget in milliseconds (default 500).
        """
        return impl_run_sampling(helpers, path, file, function, budget_ms)

    @mcp.tool()
    def mutation_run_full(path: str, file: str, function: str | None = None) -> str:
        """Deep exhaustive mutation profiling (Tier 2).

        WHEN TO USE: To verify test quality of a component. Generates all
        possible mutants, evaluates exhaustively. Slower but produces
        gateable results with full kill matrix.

        Args:
            path: Project root path.
            file: Relative path to the Python file.
            function: Optional specific function name.
        """
        return impl_run_full(helpers, path, file, function)

    @mcp.tool()
    def mutation_get_state(path: str, file: str | None = None, function: str | None = None) -> str:
        """Current mutation state and metrics.

        WHEN TO USE: To review previous mutation runs. Shows cached
        sampling/profiling results, survival rates, and coverage depth.

        Args:
            path: Project root path.
            file: Optional file to filter by.
            function: Optional function name to filter by.
        """
        return impl_get_state(helpers, path, file, function)

    @mcp.tool()
    def mutation_prescribe(path: str, file: str | None = None, function: str | None = None) -> str:
        """Deterministic prescriptions from mutation profiles.

        WHEN TO USE: After a mutation run. Analyzes survival profiles and
        recommends specific test improvements per surviving category.

        Args:
            path: Project root path.
            file: Optional file filter.
            function: Optional function filter.
        """
        return impl_prescribe(helpers, path, file, function)

    @mcp.tool()
    def mutation_decompose(
        path: str, file: str = "", function: str | None = None, mode: str = "auto"
    ) -> str:
        """Find entangled functions from mutation data.

        WHEN TO USE: For refactoring decisions. Identifies functions where
        multiple mutation categories survive, suggesting the function has
        too many responsibilities.

        Args:
            path: Project root path.
            file: File to analyze.
            function: Optional function name.
            mode: Detection mode: auto, static, or dynamic.
        """
        return impl_decompose(helpers, path, file, function, mode)

    @mcp.tool()
    def mutation_refactor_loop(path: str, file: str = "", function: str | None = None) -> str:
        """Re-profile after test improvement — close the feedback loop.

        WHEN TO USE: After writing prescribed tests. Re-runs profiling
        and computes survival rate delta.

        Args:
            path: Project root path.
            file: File to re-profile.
            function: Optional function name.
        """
        return impl_refactor_loop(helpers, path, file, function)

    @mcp.tool()
    def mutation_prescribe_tests(path: str, file: str = "", function: str | None = None) -> str:
        """Generate targeted test skeletons from mutation profiles.

        WHEN TO USE: After mutation_prescribe identifies surviving categories.
        Generates pytest test function templates targeting specific categories.

        Args:
            path: Project root path.
            file: Source file.
            function: Optional function name.
        """
        return impl_prescribe_tests(helpers, path, file, function)

    @mcp.tool()
    def mutation_validate_tests(path: str, file: str = "", function: str | None = None) -> str:
        """Re-profile and compute per-category survival deltas.

        WHEN TO USE: After writing prescribed tests. Validates that new
        tests actually killed the surviving mutants they targeted.

        Args:
            path: Project root path.
            file: Source file.
            function: Optional function name.
        """
        return impl_validate_tests(helpers, path, file, function)

    @mcp.tool()
    def mutation_clear_state(path: str, file: str | None = None) -> str:
        """Clear mutation state — use when code has drifted significantly.

        WHEN TO USE: When source code has changed substantially and cached
        mutation data is stale.

        Args:
            path: Project root path.
            file: Optional file to clear (clears all if not specified).
        """
        return impl_clear_state(helpers, path, file)

    return {
        "mutation_run_sampling": mutation_run_sampling,
        "mutation_run_full": mutation_run_full,
        "mutation_get_state": mutation_get_state,
        "mutation_prescribe": mutation_prescribe,
        "mutation_decompose": mutation_decompose,
        "mutation_refactor_loop": mutation_refactor_loop,
        "mutation_prescribe_tests": mutation_prescribe_tests,
        "mutation_validate_tests": mutation_validate_tests,
        "mutation_clear_state": mutation_clear_state,
    }
