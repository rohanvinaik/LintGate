"""Specification tools — spec_analyze, spec_prescribe, spec_composition, spec_gate_check.

Implementation functions live in _specification_helpers.py.
"""

from __future__ import annotations

from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions

from ._specification_helpers import (
    _MAX_FILES_PER_RUN,
    _MAX_TOTAL_LINES,
    impl_spec_analyze,
    impl_spec_composition,
    impl_spec_gate_check,
    impl_spec_prescribe,
    resolve_py_files,
    validate_file_in_project,
)

# Backward-compatible aliases for test imports.
_validate_file_in_project = validate_file_in_project
_resolve_py_files = resolve_py_files

# Re-export constants so monkeypatching in tests works.
__all__ = ["_MAX_FILES_PER_RUN", "_MAX_TOTAL_LINES", "register"]

# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


from mcp_tools._disk_helpers import tool_response


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register specification analysis tools on the shared MCP instance."""

    @mcp.tool()
    def spec_analyze(
        path: str,
        file: str | None = None,
        function: str | None = None,
    ) -> str:
        """Specification complexity analysis for Python functions.

        WHEN TO USE: To understand the specification complexity (sigma),
        regime classification, phase, risk level, and testability of
        functions in a project. Shows which functions are under-specified
        and where test effort should be directed.

        Example: spec_analyze(path="/my/project")
        Example: spec_analyze(path="/my/project", function="build_ledger")

        Args:
            path: Project root path.
            file: Optional specific file to analyze.
            function: Optional function name substring to filter by.
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_spec_analyze(path, file, function, helpers)
        n = len(result.get("functions", {}))
        regime_b = sum(1 for f in result.get("functions", {}).values() if isinstance(f, dict) and f.get("regime") == "B")
        avg_s = round(sum(f.get("sigma", 0) for f in result.get("functions", {}).values() if isinstance(f, dict)) / max(n, 1), 1)
        summary = f"Analyzed {n} functions. {regime_b} in regime B. Avg σ={avg_s}."
        return tool_response(result, "spec_analyze", project_root, summary, next_actions=result.get("next_actions"))

    @mcp.tool()
    def spec_prescribe(
        path: str,
        function: str | None = None,
        max_prescriptions: int = 10,
        regression_mode: bool = False,
    ) -> str:
        """Risk-prioritized test prescriptions based on specification analysis.

        WHEN TO USE: After spec_analyze reveals under-specified functions.
        Generates actionable prescriptions sorted by risk priority (P0 first),
        with specific assertion suggestions for each uncovered dimension.

        Supports expanded taxonomy: exact_value, boundary, equivalence,
        decision_table, cause_effect, property, regression.

        Example: spec_prescribe(path="/my/project")
        Example: spec_prescribe(path="/my/project", function="parse_config")
        Example: spec_prescribe(path="/my/project", regression_mode=True)

        Args:
            path: Project root path.
            function: Optional function name to prescribe for.
            max_prescriptions: Maximum prescriptions to return (default 10).
            regression_mode: Target recently-changed functions (default False).
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_spec_prescribe(path, function, max_prescriptions, regression_mode, helpers)
        prescriptions = result.get("prescriptions", [])
        top = prescriptions[0].get("function", "?") if prescriptions else "none"
        summary = f"{len(prescriptions)} prescriptions generated. Top priority: {top}."
        return tool_response(result, "spec_prescribe", project_root, summary, next_actions=result.get("next_actions"))

    @mcp.tool()
    def spec_composition(
        path: str,
        module_a: str | None = None,
        module_b: str | None = None,
    ) -> str:
        """Composition gap and sheaf condition analysis across modules.

        WHEN TO USE: To understand cross-module specification dependencies,
        integration surface complexity, and whether the sheaf condition
        (additive composition) holds for the project.

        Example: spec_composition(path="/my/project")
        Example: spec_composition(path="/my/project", module_a="core", module_b="api")

        Args:
            path: Project root path.
            module_a: Optional module path substring to filter caller side.
            module_b: Optional module path substring to filter callee side.
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_spec_composition(path, module_a, module_b, helpers)
        pairs = result.get("pairs", [])
        max_gap = max((p.get("gamma", 0) for p in pairs), default=0)
        summary = f"Composition analysis: {len(pairs)} module pairs. Max γ={round(max_gap, 2)}."
        return tool_response(result, "spec_composition", project_root, summary, next_actions=result.get("next_actions"))

    @mcp.tool()
    def spec_gate_check(
        path: str,
        file: str | None = None,
        function: str | None = None,
        hint: str | None = None,
    ) -> str:
        """Optimization gate validation with stop criteria.

        WHEN TO USE: To check whether optimization hints (cacheable,
        parallelizable, etc.) are backed by sufficient specification
        evidence. Shows which hints pass their thresholds, the delta
        to reach them, and estimated tests remaining.

        Example: spec_gate_check(path="/my/project")
        Example: spec_gate_check(path="/my/project", file="utils.py")
        Example: spec_gate_check(path="/my/project", hint="cacheable")

        Args:
            path: Project root path.
            file: Optional file to scope analysis to a single file.
            function: Optional function name to check.
            hint: Optional specific hint to filter by (e.g., "cacheable").
        """
        project_root = helpers["_validate_project_root"](path)
        result = impl_spec_gate_check(path, file, function, hint, helpers)
        gates = result.get("gates", [])
        passed = sum(1 for g in gates if g.get("passed"))
        blocked = len(gates) - passed
        summary = f"{passed}/{len(gates)} optimization hints backed by spec. {blocked} blocked."
        return tool_response(result, "spec_gate_check", project_root, summary, next_actions=result.get("next_actions"))

    @mcp.tool()
    def spec_file_analyze(
        path: str,
        file: str,
        enrich: bool = True,
    ) -> str:
        """Single-file specification analysis — fast, resource-bounded.

        WHEN TO USE: To analyze specification complexity for one file at a time.
        Faster than spec_analyze for interactive use.

        When enrich=True (default), builds property/test-effectiveness manifests
        and call graph for full analysis including purity, risk scoring, and
        assertion-based spec_level.

        When enrich=False, uses pure AST analysis only (symbolic baseline).
        No manifest dependencies — faster, but purity/risk/assertion data
        are unavailable. Useful for quick structural overview.

        Returns per-function sigma, regime, phase, risk, testability, and design
        signals for all functions in the file.

        Example: spec_file_analyze(path="/my/project", file="utils.py")
        Example: spec_file_analyze(path="/my/project", file="utils.py", enrich=False)

        Args:
            path: Project root path.
            file: Relative or absolute path to the Python file to analyze.
            enrich: Build manifests for full analysis (default True).
                Set False for AST-only symbolic baseline.
        """
        from lintgate.specification.file_analyzer import analyze_file

        project_root = helpers["_validate_project_root"](path)
        full = validate_file_in_project(project_root, file)
        result = analyze_file(full, project_root, enrich=enrich)
        output = result.to_dict()
        output["next_actions"] = serialize_next_actions(
            [
                NextAction(
                    tool="spec_file_prescribe",
                    args={"path": path, "file": file},
                    reason="Get test prescriptions for this file",
                ),
            ]
        )
        funcs = output.get("functions", {})
        n = len(funcs)
        regime_b = sum(1 for f in funcs.values() if isinstance(f, dict) and f.get("regime") == "B")
        max_s = max((f.get("sigma", 0) for f in funcs.values() if isinstance(f, dict)), default=0)
        summary = f"{n} functions in {file}. {regime_b} regime B. Max σ={round(max_s, 1)}."
        return tool_response(output, "spec_file_analyze", project_root, summary, next_actions=output.get("next_actions"))

    @mcp.tool()
    def spec_file_prescribe(
        path: str,
        file: str,
        max_prescriptions: int = 10,
    ) -> str:
        """Single-file test prescriptions — risk-prioritized for one file.

        WHEN TO USE: After spec_file_analyze shows under-specified functions
        in a file. Returns prescriptions sorted by risk priority with
        assertion suggestions.

        Example: spec_file_prescribe(path="/my/project", file="utils.py")

        Args:
            path: Project root path.
            file: Relative or absolute path to the Python file.
            max_prescriptions: Maximum prescriptions per function (default 10).
        """
        from lintgate.specification.file_analyzer import analyze_file

        project_root = helpers["_validate_project_root"](path)
        full = validate_file_in_project(project_root, file)
        result = analyze_file(
            full,
            project_root,
            include_prescriptions=True,
            max_prescriptions=max_prescriptions,
        )
        output = result.to_dict()
        output["next_actions"] = serialize_next_actions(
            [
                NextAction(
                    tool="mutation_run_sampling",
                    args={"path": path, "file": file},
                    reason="Run mutation sampling to empirically verify prescriptions",
                ),
                NextAction(
                    tool="spec_file_analyze",
                    args={"path": path, "file": file},
                    reason="View full specification analysis for this file",
                ),
            ]
        )
        n = len(output.get("prescriptions", output.get("functions", {})))
        summary = f"{n} prescriptions for {file}."
        return tool_response(output, "spec_file_prescribe", project_root, summary, next_actions=output.get("next_actions"))

    @mcp.tool()
    def spec_project_rollup(
        path: str,
        use_cache: bool = True,
        analyze_uncached: bool = False,
        include_tests: bool = False,
    ) -> str:
        """Project-wide specification rollup with file-level caching.

        WHEN TO USE: To get a high-level overview of specification health
        across the entire project. Aggregates per-file analysis into totals
        for sigma, regime/risk/phase distributions, and hotspot files.

        Default mode is cache-read-only: reads existing cache entries and
        reports cache_misses for files not yet analyzed. Use
        analyze_uncached=True to analyze missing files live (slower).
        By default, test files are excluded so hotspot ranking focuses on
        production code. Set include_tests=True to include test files.

        Example: spec_project_rollup(path="/my/project")
        Example: spec_project_rollup(path="/my/project", analyze_uncached=True)
        Example: spec_project_rollup(path="/my/project", include_tests=True)

        Args:
            path: Project root path.
            use_cache: Use file-level content-hash caching (default True).
            analyze_uncached: Analyze files with no cache entry (default False).
            include_tests: Include test files in aggregation (default False).
        """
        from lintgate.specification.project_rollup import rollup_project

        project_root = helpers["_validate_project_root"](path)
        rollup = rollup_project(
            project_root,
            use_cache=use_cache,
            analyze_uncached=analyze_uncached,
            include_tests=include_tests,
        )
        output = rollup.to_dict()
        output["next_actions"] = serialize_next_actions(
            [
                NextAction(
                    tool="spec_file_analyze",
                    args={"path": path, "file": "<hotspot_file>"},
                    reason="Drill into a hotspot file for per-function details",
                ),
                NextAction(
                    tool="spec_prescribe",
                    args={"path": path},
                    reason="Get test prescriptions for under-specified functions",
                ),
            ]
        )
        n = output.get("files_analyzed", 0)
        avg = round(output.get("avg_sigma", 0), 1)
        hotspots = len(output.get("hotspots", []))
        summary = f"Project rollup: {n} files. Avg σ={avg}. {hotspots} hotspot files."
        return tool_response(output, "spec_project_rollup", project_root, summary, next_actions=output.get("next_actions"))

    return {
        "spec_analyze": spec_analyze,
        "spec_prescribe": spec_prescribe,
        "spec_composition": spec_composition,
        "spec_gate_check": spec_gate_check,
        "spec_file_analyze": spec_file_analyze,
        "spec_file_prescribe": spec_file_prescribe,
        "spec_project_rollup": spec_project_rollup,
    }
