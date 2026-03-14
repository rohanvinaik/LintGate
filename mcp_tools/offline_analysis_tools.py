"""Offline analysis tools — comprehensive LLM-free project analysis.

2 MCP tools:
- offline_analysis_generate: Generate a Colab/Jupyter notebook for full offline analysis
- offline_analysis_run: Run the full analysis locally and save results
"""

from __future__ import annotations

from typing import Any

from ._offline_analysis_impl import (
    impl_offline_analysis_generate,
    impl_offline_analysis_run,
)


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register offline analysis tools on the shared MCP instance."""

    @mcp.tool()
    def offline_analysis_generate(
        path: str,
        workers: int = 4,
        mutation_budget_ms: int = 500,
        output: str = "",
        include_mutation: bool = True,
    ) -> str:
        """Generate a Jupyter notebook for comprehensive offline project analysis.

        WHEN TO USE: When you want to run a full LintGate analysis on Google Colab
        or any Jupyter environment. Produces a self-contained .ipynb that clones the
        repo, runs all analysis channels (lint + specification + composition +
        performance + test coverage + optional mutation profiling), and outputs a
        portable JSON artifact with a prioritized, dependency-ordered action plan.

        The output action_plan.json can be passed directly to an LLM coding agent
        with "implement the fixes in this action plan, starting from rank 1."

        Example: offline_analysis_generate(path="/my/project")
        Example: offline_analysis_generate(path="/my/project", include_mutation=False)

        Args:
            path: Project root path.
            workers: Parallel workers for Colab mutation profiling (default 4).
            mutation_budget_ms: Per-function mutation budget in ms (default 500).
            output: Custom output path for the notebook (default: scripts/).
            include_mutation: Include mutation profiling in analysis (default True, slower).
        """
        return impl_offline_analysis_generate(
            helpers, path, workers, mutation_budget_ms, output, include_mutation,
        )

    @mcp.tool()
    def offline_analysis_run(
        path: str,
        include_mutation: bool = True,
        output: str = "",
    ) -> str:
        """Run comprehensive offline analysis locally and save results.

        WHEN TO USE: When you want to run the full analysis pipeline right now
        on the local machine instead of generating a notebook. Produces the same
        comprehensive JSON artifact as the notebook but runs immediately.

        Returns a summary with top actions and the path to the full analysis file.
        Pass the output file to an LLM agent for systematic implementation.

        Example: offline_analysis_run(path="/my/project")
        Example: offline_analysis_run(path="/my/project", include_mutation=False)

        Args:
            path: Project root path.
            include_mutation: Include cached mutation data in analysis (default True).
            output: Custom output path for the JSON result (default: .lintgate/).
        """
        return impl_offline_analysis_run(helpers, path, include_mutation, output)

    return {
        "offline_analysis_generate": offline_analysis_generate,
        "offline_analysis_run": offline_analysis_run,
    }
