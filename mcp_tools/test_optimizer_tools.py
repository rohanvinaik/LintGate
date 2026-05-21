"""Test optimizer tools — test_suite_triage and test_suite_compact MCP surface."""

from __future__ import annotations

from typing import Any

from ._test_optimizer_impl import impl_test_compact, impl_test_triage


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register test optimizer tools on the shared MCP instance."""

    @mcp.tool()
    def test_suite_triage(path: str, file: str) -> str:
        """Diagnose test redundancy via mutation convergence analysis.

        Extracts the minimum killing set — the smallest subset of tests that
        achieves the same mutation kill rate as the full suite. Requires prior
        mutation profiling (run improve_tests first).

        Args:
            path: Project root.
            file: Source file to triage tests for (e.g., "src/core.py").
        """
        return impl_test_triage(path=path, file=file)

    @mcp.tool()
    def test_suite_compact(path: str, file: str, dry_run: bool = True) -> str:
        """Compact a test file to its minimum killing set.

        AST-extracts only the tests that contribute unique mutation kills,
        preserving fixtures, helpers, and imports. Drops specification-
        redundant tests. Dry-run by default.

        Args:
            path: Project root.
            file: Source file whose tests to compact.
            dry_run: Preview without writing (default True).
        """
        return impl_test_compact(path=path, file=file, dry_run=dry_run)

    return {
        "test_suite_triage": test_suite_triage,
        "test_suite_compact": test_suite_compact,
    }
