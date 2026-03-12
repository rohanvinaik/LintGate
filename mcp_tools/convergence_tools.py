"""Convergence tools — analyze, plan extractions, and view optimization landscape."""

from __future__ import annotations

import ast
import json
import os
from typing import Any

from lintgate.next_action import NextAction, serialize_next_actions

# Patterns for filtering non-production files from optimization targets
_NON_PRODUCTION_PATTERNS = (
    "test_", "tests/", "test/", "_test.py", "conftest.py",
    "fuzz_", "fuzz/", "benchmark_", "benchmarks/",
    "fixture", "testutil", "test_helper",
)


def _is_production_file(filepath: str) -> bool:
    """Return True if filepath looks like production code, not test/fuzz/fixture."""
    basename = os.path.basename(filepath)
    rel = filepath.replace("\\", "/")
    for pat in _NON_PRODUCTION_PATTERNS:
        if pat.endswith("/"):
            if f"/{pat}" in f"/{rel}":
                return False
        elif basename.startswith(pat) or basename.endswith(pat):
            return False
    return True


def _build_channels() -> list:
    """Instantiate analysis channels needed for convergence."""
    from lintgate.channels.lint_channel import LintChannel
    from lintgate.channels.performance_channel import PerformanceChannel
    from lintgate.channels.structure_channel import StructureChannel
    from lintgate.channels.test_channel import TestChannel

    return [LintChannel(), TestChannel(), StructureChannel(), PerformanceChannel()]


def _discover_python_files(project_root: str, file_filter: str | None = None) -> list[str]:
    """Discover Python files in a project, optionally filtered."""
    from lintgate.discovery import discover_project_files

    if file_filter:
        full = (
            os.path.join(project_root, file_filter)
            if not os.path.isabs(file_filter)
            else file_filter
        )
        return [full] if os.path.isfile(full) else []

    return discover_project_files(project_root, extra_exclude_dirs=frozenset({"archive"}))


def _impl_convergence_analyze(
    path: str,
    file: str | None,
    function: str | None,
    helpers: Any,
) -> dict[str, Any]:
    """Run multi-lens convergence aggregation."""
    import contextlib

    from lintgate.convergence.integration import (
        extract_all_evidence,
        extract_file_evidence,
    )

    helpers["_validate_project_root"](path)

    # If a file filter was specified but resolved to nothing, fail explicitly
    # instead of silently falling back to full-project analysis.
    if file is not None:
        filtered = _discover_python_files(path, file)
        if not filtered:
            return {
                "project": path,
                "error": f"File not found: {file}",
            }

    # Run controlplane to get channel results
    results: list = []
    with contextlib.suppress(Exception):
        from lintgate.controlplane.runtime import run_mesh
        from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent

        py_files = _discover_python_files(path, file)
        event = SupervisionEvent(
            project_root=path,
            surface="mcp",
            files_changed=py_files[:20],  # Cap for performance
        )
        config = ControlPlaneConfig()
        channels = _build_channels()
        mesh = run_mesh(event, config, channels)
        results = mesh.channel_results

    if not results:
        return {
            "project": path,
            "error": "No channel results available. Ensure project has Python files.",
        }

    # Function-level convergence
    func_convergence = extract_all_evidence(results)

    # File-level convergence
    file_convergence = extract_file_evidence(results)

    # Apply filters
    if function:
        func_convergence = [r for r in func_convergence if function.lower() in r.target.lower()]
    if file:
        file_convergence = [r for r in file_convergence if file in r.target]

    return {
        "project": path,
        "function_convergence": {
            "total": len(func_convergence),
            "results": [r.to_dict() for r in func_convergence[:10]],
        },
        "file_convergence": {
            "total": len(file_convergence),
            "results": [r.to_dict() for r in file_convergence[:10]],
        },
        "next_actions": serialize_next_actions(
            [
                NextAction(
                    tool="extraction_plan",
                    args={"path": path},
                    reason="Convergence shows EXTRACT actionability for one or more functions.",
                    priority=2,
                    condition="convergence shows EXTRACT actionability",
                ),
                NextAction(
                    tool="optimization_landscape",
                    args={"path": path},
                    reason="View project-wide optimization potential.",
                    priority=4,
                    condition="want project-wide optimization view",
                ),
            ]
        ),
    }


def _impl_extraction_plan(
    path: str,
    function: str,
    helpers: Any,
) -> dict[str, Any]:
    """Build a stepwise extraction plan for a function."""
    from lintgate.convergence.evidence import Actionability, ConvergenceResult
    from lintgate.convergence.extraction_plan import build_extraction_plan
    from lintgate.convergence.projector import project_post_extraction

    helpers["_validate_project_root"](path)

    # Find the function in convergence results
    candidate = None
    source_ast = None

    # Try to build convergence for the specific function
    try:
        from lintgate.controlplane.runtime import run_mesh
        from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
        from lintgate.convergence.integration import extract_all_evidence

        # Determine source file from function target
        source_file = function.split("::")[0] if "::" in function else ""
        files = (
            [os.path.join(path, source_file)] if source_file else _discover_python_files(path)[:10]
        )

        event = SupervisionEvent(project_root=path, surface="mcp", files_changed=files)
        config = ControlPlaneConfig()
        channels = _build_channels()
        mesh = run_mesh(event, config, channels)

        convergence = extract_all_evidence(mesh.channel_results)
        for cr in convergence:
            if function.lower() in cr.target.lower():
                candidate = cr
                break
    except Exception:
        pass

    if candidate is None:
        # Create a minimal convergence result for the function
        candidate = ConvergenceResult(
            target=function,
            support_prob=0.5,
            oppose_prob=0.0,
            net_confidence=0.5,
            supporting_lenses=[],
            opposing_lenses=[],
            actionability=Actionability.INVESTIGATE,
        )

    # Parse source AST if available
    source_file = function.split("::")[0] if "::" in function else ""
    if source_file:
        full_path = (
            os.path.join(path, source_file) if not os.path.isabs(source_file) else source_file
        )
        try:
            with open(full_path, encoding="utf-8") as f:
                source_ast = ast.parse(f.read(), filename=full_path)
        except (OSError, SyntaxError):
            pass

    plan = build_extraction_plan(candidate, source_ast, source_file)

    # Project post-extraction opportunities
    opportunities = project_post_extraction(plan, source_ast)
    plan.post_extraction_opportunities = opportunities

    result = plan.to_dict()
    result["next_actions"] = serialize_next_actions(
        [
            NextAction(
                tool="optimization_landscape",
                args={"path": path},
                reason="View project-wide optimization potential after extraction.",
                priority=3,
                condition="want to see project-wide optimization view",
            ),
            NextAction(
                tool="convergence_analyze",
                args={"path": path},
                reason="See convergence evidence for other functions.",
                priority=4,
                condition="want to see evidence for other functions",
            ),
        ]
    )
    return result


def _collect_manifest_hints(manifest: Any) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """Extract cache, parallel, and extraction hints from manifest pure functions."""
    cache_hotspots: list[dict[str, Any]] = []
    parallel_opportunities: list[dict[str, Any]] = []
    extraction_safe: list[dict[str, Any]] = []

    for name, func in manifest.functions.items():
        if not func.purity.is_pure:
            continue

        entry: dict[str, Any] = {
            "function": name,
            "source_file": func.source_file or "",
            "confidence": func.purity.confidence,
        }

        hints = set(func.optimization_hints)
        if "cacheable" in hints:
            cache_hotspots.append({**entry, "hints": list(hints)})
        if "parallelizable" in hints or "map-reduce-compatible" in hints:
            parallel_opportunities.append({
                "pattern": "MANIFEST_HINT",
                "file": func.source_file or "",
                "line": 0,
                "callee": name,
                "confidence": func.purity.confidence,
                "constraints": [],
                "detail": f"Manifest hints: {sorted(hints)}",
            })
        if func.extraction_safety == "safe" and func.properties:
            extraction_safe.append({
                **entry,
                "properties": [p.kind.value for p in func.properties],
                "extraction_safety": func.extraction_safety,
            })

    return cache_hotspots, parallel_opportunities, extraction_safe


def _run_detectors_on_file(
    tree: ast.AST,
    rel_path: str,
    cache_hotspots: list[dict[str, Any]],
    parallel_opportunities: list[dict[str, Any]],
    jit_candidates: list[dict[str, Any]],
) -> None:
    """Run cache/parallel/JIT detectors on a single parsed file, appending results in-place."""
    try:
        from lintgate.linters.performance_checks.cache_scoring import score_all_cacheable
        from lintgate.linters.performance_checks.purity import analyze_purity

        purity = analyze_purity(tree)
        for qname, cs in score_all_cacheable(tree, purity).items():
            if cs.band in ("HIGH", "MEDIUM"):
                cache_hotspots.append({
                    "function": qname,
                    "source_file": rel_path,
                    "cache_score": cs.score,
                    "cache_band": cs.band,
                    "cache_factors": cs.factors,
                })
    except ImportError:
        pass

    try:
        from lintgate.linters.performance_checks.parallel_detector import (
            detect_parallel_opportunities,
        )
        from lintgate.linters.performance_checks.purity import analyze_purity

        purity = analyze_purity(tree)
        for opp in detect_parallel_opportunities(tree, purity, file_path=rel_path):
            parallel_opportunities.append(opp.to_dict())
    except ImportError:
        pass

    try:
        from lintgate.linters.performance_checks.jit_detector import detect_jit_candidates
        from lintgate.linters.performance_checks.purity import analyze_purity

        purity = analyze_purity(tree)
        for c in detect_jit_candidates(tree, purity, file_path=rel_path):
            jit_candidates.append(c.to_dict())
    except ImportError:
        pass


def _dedupe_cache_hotspots(cache_hotspots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate cache hotspots by (source_file, function), keeping highest-scored."""
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for ch in sorted(
        cache_hotspots,
        key=lambda x: x.get("cache_score", x.get("confidence", 0)),
        reverse=True,
    ):
        key = (ch.get("source_file", ""), ch["function"])
        if key not in seen:
            seen.add(key)
            deduped.append(ch)
    return deduped


def _build_static_landscape(
    path: str,
) -> dict[str, Any]:
    """Build a static optimization landscape from manifest data alone.

    No convergence or mutation state required — uses purity analysis,
    cache scoring, parallel detection, and JIT detection directly.
    """
    from lintgate.channels.performance_channel import _discover_python_files
    from lintgate.linters.performance_checks.manifest import build_manifest

    all_files = _discover_python_files(path)
    py_files = [f for f in all_files if _is_production_file(os.path.relpath(f, path))]
    if not py_files:
        return {"project": path, "error": "No Python files found."}

    manifest = build_manifest(path, py_files)
    if manifest is None:
        return {"project": path, "error": "Failed to build manifest."}

    cache_hotspots, parallel_opportunities, extraction_safe = _collect_manifest_hints(manifest)
    jit_candidates: list[dict[str, Any]] = []

    for fpath in py_files[:30]:
        try:
            with open(fpath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fpath)
        except (OSError, SyntaxError):
            continue
        _run_detectors_on_file(
            tree, os.path.relpath(fpath, path),
            cache_hotspots, parallel_opportunities, jit_candidates,
        )

    deduped_cache = _dedupe_cache_hotspots(cache_hotspots)

    return {
        "project": path,
        "mode": "static",
        "cache_hotspots": deduped_cache[:20],
        "parallel_opportunities": parallel_opportunities[:20],
        "jit_candidates": jit_candidates[:10],
        "extraction_safe_refactors": extraction_safe[:15],
        "summary": {
            "total_functions": manifest.pure_count + manifest.impure_count,
            "pure_functions": manifest.pure_count,
            "cache_candidates": len(deduped_cache),
            "parallel_opportunities": len(parallel_opportunities),
            "jit_candidates": len(jit_candidates),
        },
    }


def _impl_optimization_landscape(
    path: str,
    helpers: Any,
    *,
    mode: str = "auto",
) -> dict[str, Any]:
    """Return project-wide optimization opportunity map.

    Args:
        mode: "auto" (dynamic then static fallback), "static" (manifest-only),
              "dynamic" (convergence-based, original behavior).
    """
    import contextlib

    from lintgate.convergence.extraction_plan import build_extraction_plan
    from lintgate.convergence.integration import extract_all_evidence
    from lintgate.convergence.projector import project_post_extraction
    from lintgate.convergence.synthesizer import synthesize_landscape

    helpers["_validate_project_root"](path)

    # Static mode — skip convergence entirely
    if mode == "static":
        result = _build_static_landscape(path)
        result["next_actions"] = serialize_next_actions(
            [
                NextAction(
                    tool="convergence_analyze",
                    args={"path": path},
                    reason="Run convergence analysis for richer dynamic landscape.",
                    priority=3,
                    condition="want dynamic convergence data",
                ),
            ]
        )
        return result

    # Dynamic mode — run convergence analysis
    results: list = []
    convergence = []
    with contextlib.suppress(Exception):
        from lintgate.controlplane.runtime import run_mesh
        from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent

        all_files = _discover_python_files(path)
        # Filter out test/fuzz/fixture files — landscape targets production code only
        py_files = [f for f in all_files if _is_production_file(os.path.relpath(f, path))][:30]
        event = SupervisionEvent(project_root=path, surface="mcp", files_changed=py_files[:5])
        # Pre-populate python_files so the prepass uses our production-filtered
        # list instead of falling back to full-project discovery (which would
        # re-introduce test files). The prepass honors pre-populated values.
        event.context["python_files"] = py_files
        config = ControlPlaneConfig()
        channels = _build_channels()
        mesh = run_mesh(event, config, channels)
        results = mesh.channel_results

    if results:
        convergence = extract_all_evidence(results)

    if not convergence:
        if mode == "dynamic":
            return {
                "project": path,
                "mode": "dynamic",
                "diagnostics": {
                    "reason": "No convergence data produced by dynamic analysis.",
                    "suggestion": "Use mode='static' for manifest-based landscape, "
                    "or run controlplane_run first for richer data.",
                },
            }
        # Auto mode — fall back to static
        result = _build_static_landscape(path)
        result["mode"] = "auto (static fallback)"
        result["diagnostics"] = {
            "reason": "Dynamic convergence produced no data; fell back to static manifest analysis.",
        }
        result["next_actions"] = serialize_next_actions(
            [
                NextAction(
                    tool="controlplane_run",
                    args={"path": path},
                    reason="Run controlplane for richer convergence data.",
                    priority=2,
                ),
            ]
        )
        return result

    # Build plans for top convergence targets
    plans = []
    for cr in convergence[:10]:
        source_file = cr.target.split("::")[0] if "::" in cr.target else ""
        source_ast = None
        if source_file:
            full_path = (
                os.path.join(path, source_file) if not os.path.isabs(source_file) else source_file
            )
            try:
                with open(full_path, encoding="utf-8") as f:
                    source_ast = ast.parse(f.read(), filename=full_path)
            except (OSError, SyntaxError):
                pass

        plan = build_extraction_plan(cr, source_ast, source_file)
        opportunities = project_post_extraction(plan, source_ast)
        plan.post_extraction_opportunities = opportunities
        plans.append(plan)

    landscape = synthesize_landscape(convergence, plans)

    result = landscape.to_dict()
    result["project"] = path
    result["mode"] = "dynamic"
    result["convergence_targets"] = len(convergence)
    result["plans_built"] = len(plans)
    result["next_actions"] = serialize_next_actions(
        [
            NextAction(
                tool="extraction_plan",
                args={"path": path},
                reason="Drill into extraction plan for a specific high-convergence function.",
                priority=2,
                condition="drill into specific function",
            ),
            NextAction(
                tool="convergence_analyze",
                args={"path": path},
                reason="See detailed convergence evidence.",
                priority=4,
                condition="see detailed evidence",
            ),
        ]
    )
    return result


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register convergence analysis tools on the shared MCP instance."""

    @mcp.tool()
    def convergence_analyze(
        path: str,
        file: str | None = None,
        function: str | None = None,
    ) -> str:
        """Run multi-lens convergence aggregation on a project.

        WHEN TO USE: After controlplane_run identifies decomposition candidates,
        or when you want to understand which functions/files have convergent
        evidence for extraction or splitting from multiple analysis lenses.

        Returns per-function and per-file ConvergenceResults with contributing
        lenses, net confidence, and actionability (EXTRACT/SPLIT/INVESTIGATE).

        Example: convergence_analyze(path="/my/project")
        Example: convergence_analyze(path="/my/project", function="process_data")
        Example: convergence_analyze(path="/my/project", file="utils.py")

        Args:
            path: Project root path.
            file: Optional file path filter.
            function: Optional function name substring filter.
        """
        return json.dumps(_impl_convergence_analyze(path, file, function, helpers))

    @mcp.tool()
    def extraction_plan(
        path: str,
        function: str,
    ) -> str:
        """Build a stepwise extraction plan for a specific function.

        WHEN TO USE: After convergence_analyze shows a function with EXTRACT
        actionability, use this to get ordered steps with function signatures,
        parameter specs, importer update lists, and test migration guidance.

        Returns ordered ExtractionSteps and post-extraction projected
        opportunities (cacheable, parallelizable, directly_testable).

        Example: extraction_plan(path="/my/project", function="utils.py::process_data")

        Args:
            path: Project root path.
            function: Target function (format: "file.py::function_name").
        """
        return json.dumps(_impl_extraction_plan(path, function, helpers))

    @mcp.tool()
    def optimization_landscape(
        path: str,
        mode: str = "auto",
    ) -> str:
        """Return project-wide optimization opportunity map.

        WHEN TO USE: When you want a strategic view of the codebase's
        optimization potential — which functions to cache, which to
        parallelize, which extractions unlock the most value.

        Shows cacheable functions, parallelizable groups, JIT candidates,
        extraction dependency order, and aggregate impact metrics.

        Example: optimization_landscape(path="/my/project")
        Example: optimization_landscape(path="/my/project", mode="static")

        Args:
            path: Project root path.
            mode: Analysis mode — "auto" (dynamic with static fallback),
                "static" (manifest-only, no convergence needed),
                "dynamic" (convergence-based, requires prior runs).
        """
        return json.dumps(_impl_optimization_landscape(path, helpers, mode=mode))

    return {
        "convergence_analyze": convergence_analyze,
        "extraction_plan": extraction_plan,
        "optimization_landscape": optimization_landscape,
    }
