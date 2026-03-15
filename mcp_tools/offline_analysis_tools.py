"""Offline analysis tools — comprehensive LLM-free project analysis.

2 MCP tools:
- offline_analysis_generate: Generate a Colab/Jupyter notebook for offline analysis
- offline_analysis_run: Run analysis locally and save results

Both support 5 modes:
- "full" (default): lint + spec + mutation + composition + performance + coverage
- "controlplane": full 6-channel supervision mesh
- "decomposition": extraction guidance + composition gaps + refactor targets
- "platonic": convergence roadmap (distance from platonic ideal per file)
- "complete": everything combined (all of the above)
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
        mode: str = "full",
        workers: int = 4,
        mutation_budget_ms: int = 500,
        output: str = "",
        include_mutation: bool = True,
    ) -> str:
        """Generate a Jupyter notebook for offline project analysis.

        WHEN TO USE: When you want to run LintGate analysis on Google Colab
        or any Jupyter environment without an LLM. Produces a self-contained
        .ipynb that clones the repo, runs analysis, and outputs a portable
        JSON artifact with a prioritized action plan.

        CHAIN: generate notebook → upload to Colab → run all → download JSON → pass to LLM agent
        COST: Depends on mode. "full" ~2-5min, "complete" ~5-15min, "controlplane" ~1-3min.

        Modes:
        - "full": lint + specification + mutation + composition + performance + test coverage
        - "controlplane": full 6-channel supervision mesh (lint + tests + deps + git + behavior + structure)
        - "decomposition": extraction guidance, composition gaps, refactor targets
        - "platonic": convergence roadmap — ranks files by distance from platonic ideal
        - "complete": EVERYTHING combined (all modes in one artifact)

        Example: offline_analysis_generate(path="/my/project")
        Example: offline_analysis_generate(path="/my/project", mode="controlplane")
        Example: offline_analysis_generate(path="/my/project", mode="complete")
        Example: offline_analysis_generate(path="/my/project", mode="platonic", include_mutation=False)

        Args:
            path: Project root path.
            mode: Analysis mode (default "full"). See above.
            workers: Parallel workers for Colab mutation profiling (default 4).
            mutation_budget_ms: Per-function mutation budget in ms (default 500).
            output: Custom output path for the notebook (default: scripts/).
            include_mutation: Include mutation profiling (default True, slower).
        """
        return impl_offline_analysis_generate(
            helpers, path, workers, mutation_budget_ms, output, include_mutation, mode=mode,
        )

    @mcp.tool()
    def offline_analysis_run(
        path: str,
        mode: str = "full",
        include_mutation: bool = True,
        output: str = "",
    ) -> str:
        """Run offline analysis locally and save results.

        WHEN TO USE: When you want to run the analysis pipeline right now
        on the local machine. Produces a JSON artifact with a prioritized
        action plan. Pass the output file to an LLM agent for systematic fixes.

        CHAIN: run → read JSON → implement action plan from rank 1
        COST: "full" <5s, "controlplane" ~30-60s, "complete" ~60-120s.

        Modes: "full", "controlplane", "decomposition", "platonic", "complete".
        See offline_analysis_generate for mode descriptions.

        Example: offline_analysis_run(path="/my/project")
        Example: offline_analysis_run(path="/my/project", mode="complete")
        Example: offline_analysis_run(path="/my/project", mode="decomposition")

        Args:
            path: Project root path.
            mode: Analysis mode (default "full").
            include_mutation: Include cached mutation data (default True).
            output: Custom output path for JSON result (default: .lintgate/).
        """
        return impl_offline_analysis_run(helpers, path, include_mutation, output, mode=mode)

    return {
        "offline_analysis_generate": offline_analysis_generate,
        "offline_analysis_run": offline_analysis_run,
    }
