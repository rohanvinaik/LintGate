"""Bootstrap tools — trigger and monitor the test bootstrap pipeline."""

from __future__ import annotations

from typing import Any


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register bootstrap tools on the shared MCP instance."""

    @mcp.tool()
    def bootstrap_tests(
        path: str,
        force: bool = False,
        dry_run: bool = False,
    ) -> str:
        """Manually trigger test bootstrap pipeline.

        WHEN TO USE: On cold-start projects with zero or few tests.
        Generates test skeletons, property tests for pure functions,
        and behavioral contracts — all deterministic, no LLM calls.

        Generated tests go into tests/generated/ (isolated namespace).
        Use dry_run=True to preview what would be generated.

        Example: bootstrap_tests(path="/my/project")
        Example: bootstrap_tests(path="/my/project", dry_run=True)

        Args:
            path: Project root path.
            force: Overwrite existing generated test files (default False).
            dry_run: Preview changes without writing (default False).
        """
        project_root = helpers["_validate_project_root"](path)

        from lintgate.orchestration.bootstrap_pipeline import BootstrapPipeline

        pipeline = BootstrapPipeline(project_root)
        result = pipeline.run(dry_run=dry_run, force=force)

        return helpers["_json_dumps"](result.to_dict())  # type: ignore[no-any-return]

    @mcp.tool()
    def bootstrap_status(path: str) -> str:
        """Check bootstrap pipeline status, phase, artifacts, errors.

        WHEN TO USE: After triggering bootstrap_tests, or to check
        if a bootstrap has been run for the project.

        Example: bootstrap_status(path="/my/project")

        Args:
            path: Project root path.
        """
        project_root = helpers["_validate_project_root"](path)

        from lintgate.orchestration.bootstrap_state import BootstrapState

        state = BootstrapState.load(project_root)
        return helpers["_json_dumps"](state.to_summary())  # type: ignore[no-any-return]

    return {
        "bootstrap_tests": bootstrap_tests,
        "bootstrap_status": bootstrap_status,
    }
