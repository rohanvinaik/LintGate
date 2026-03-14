"""Safe module refactoring tool — libcst-based import rewriting (#refactor_move).

1 MCP tool:
- refactor_move: Move a module with automatic import rewriting
"""

from __future__ import annotations


def register(mcp, helpers):
    """Register refactor_move tools on the shared MCP instance."""

    @mcp.tool()
    def refactor_move(
        path: str,
        source: str,
        destination: str,
        dry_run: bool = True,
        generate_shim: bool = True,
    ) -> str:
        """Move a Python module with automatic import rewriting.

        Scans the project for all imports of the source module and rewrites
        them to point to the destination. Uses libcst for precise AST-based
        rewriting when available; falls back to showing what needs changing
        via ast scan when libcst is not installed.

        String references in import-bearing call sites (mock.patch,
        monkeypatch.setattr, importlib.import_module) are also rewritten.
        Arbitrary string literals are NOT touched.

        Args:
            path: Project root path.
            source: Dotted module path to move FROM (e.g., "lintgate.old_module").
            destination: Dotted module path to move TO (e.g., "lintgate.new_module").
            dry_run: If True (default), only scan and report — no files changed.
            generate_shim: If True (default), generate a backward-compatibility
                shim at the old location after applying the move.
        """
        from mcp_tools._refactor_move_impl import impl_refactor_move

        return impl_refactor_move(helpers, path, source, destination, dry_run, generate_shim)

    return {"refactor_move": refactor_move}
