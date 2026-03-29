"""Bootstrap tools — trigger and monitor the test bootstrap pipeline."""

from __future__ import annotations

from typing import Any

from mcp_tools._disk_helpers import tool_response


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
        result_dict = result.to_dict()

        generated = result_dict.get("tests_generated", 0)
        status = result_dict.get("status", "unknown")
        bt_summary = f"Bootstrap: {status}. {generated} tests generated."
        return tool_response(
            result_dict, "bootstrap_tests", project_root, bt_summary,
            next_actions=result_dict.get("next_actions"),
            extra={"status": status, "tests_generated": generated},
        )

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
        result_dict = state.to_summary()
        phase = result_dict.get("phase", "unknown")
        bs_summary = f"Bootstrap status: {phase}."
        return tool_response(
            result_dict, "bootstrap_status", project_root, bs_summary,
            next_actions=result_dict.get("next_actions"),
            extra={"phase": phase},
        )

    return {
        "bootstrap_tests": bootstrap_tests,
        "bootstrap_status": bootstrap_status,
    }
