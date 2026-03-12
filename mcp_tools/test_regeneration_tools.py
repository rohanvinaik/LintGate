"""MCP tools for test regeneration workflow.

Four-step workflow:
1. test_rebuild_plan — classify functions, build manifest
2. test_rebuild_generate — generate tests for auto_generate_unit targets
3. test_rebuild_validate — validate generated tests against gates
4. test_rebuild_apply — promote generated tests, quarantine old ones

Implementation in _test_regeneration_impl.py.
"""

from __future__ import annotations

from typing import Any

from ._test_regeneration_gates import impl_rebuild_apply, impl_rebuild_validate
from ._test_regeneration_impl import impl_rebuild_generate, impl_rebuild_plan


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    @mcp.tool()
    def test_rebuild_plan(
        path: str,
        file: str | None = None,
        write_manifest: bool = True,
        preserve_globs: list[str] | None = None,
    ) -> str:
        """Build a test rebuild manifest by classifying every function.

        WHEN TO USE: First step of test regeneration. Analyzes all project
        functions using spec analysis and mutation cache, classifies each
        into a strategy (exclude_mutation, preserve_system, manual_contract,
        auto_generate_unit), and writes a manifest.

        Never deletes anything. The manifest is a plan, not an action.

        Args:
            path: Project root path.
            file: Optional file filter (relative path). If set, only
                analyzes functions in this file.
            write_manifest: Write manifest to .lintgate/test_rebuild_manifest.json.
            preserve_globs: Glob patterns for test files to always preserve.
        """
        return impl_rebuild_plan(
            helpers, path, file, write_manifest, preserve_globs
        )

    @mcp.tool()
    def test_rebuild_generate(
        path: str,
        write: bool = False,
        max_files: int = 50,
    ) -> str:
        """Generate tests for auto_generate_unit targets in the manifest.

        WHEN TO USE: After test_rebuild_plan. Generates test files for
        functions classified as auto_generate_unit. Uses skeleton generation,
        input inference, and mutation prescriptions.

        Generated tests go to tests/generated/ — never overwrites existing tests.

        Args:
            path: Project root path.
            write: Actually write test files to disk.
            max_files: Maximum number of source files to generate tests for.
        """
        return impl_rebuild_generate(helpers, path, write, max_files)

    @mcp.tool()
    def test_rebuild_validate(
        path: str,
        review_ceiling: float = 0.15,
    ) -> str:
        """Validate generated tests against quality gates.

        WHEN TO USE: After test_rebuild_generate with write=True.
        Runs import/pytest sanity, mutation validation, and checks
        that all gates pass before allowing apply.

        Args:
            path: Project root path.
            review_ceiling: Maximum share of manual_review_required.
        """
        return impl_rebuild_validate(helpers, path, review_ceiling)

    @mcp.tool()
    def test_rebuild_apply(
        path: str,
        dry_run: bool = True,
    ) -> str:
        """Apply the test rebuild: promote generated, quarantine old.

        WHEN TO USE: After test_rebuild_validate returns ready_to_apply=true.
        This is the only destructive step.

        With dry_run=True (default), shows what would happen without acting.
        With dry_run=False, moves quarantined tests and promotes generated ones.

        Args:
            path: Project root path.
            dry_run: Preview changes without acting (default True).
        """
        return impl_rebuild_apply(helpers, path, dry_run)

    return {
        "test_rebuild_plan": test_rebuild_plan,
        "test_rebuild_generate": test_rebuild_generate,
        "test_rebuild_validate": test_rebuild_validate,
        "test_rebuild_apply": test_rebuild_apply,
    }
