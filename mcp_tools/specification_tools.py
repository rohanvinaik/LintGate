"""Specification tools — spec_analyze, spec_prescribe, spec_composition, spec_gate_check."""

from __future__ import annotations

import os
from typing import Any

from lintgate.discovery import discover_project_files
from lintgate.next_action import NextAction, serialize_next_actions

# Hard guardrails — fail closed, never silently truncate.
_MAX_FILES_PER_RUN = 500
_MAX_TOTAL_LINES = 500_000

# Extra directories to exclude beyond canonical discovery for spec analysis.
_SPEC_EXTRA_EXCLUDE = frozenset({"archive"})


def _resolve_py_files(project_root: str, file: str | None) -> list[str] | dict[str, Any]:
    """Resolve file list: single file if specified, canonical discovery otherwise.

    Returns a list of paths on success, or an error dict on failure.
    The error dict should be returned directly to the caller.
    """
    if file:
        full = os.path.join(project_root, file) if not os.path.isabs(file) else file
        if not os.path.isfile(full):
            return {"error": f"File not found: {file}"}
        return [full]

    py_files = discover_project_files(project_root, extra_exclude_dirs=_SPEC_EXTRA_EXCLUDE)

    if len(py_files) > _MAX_FILES_PER_RUN:
        return {
            "error": "File budget exceeded — use the `file` parameter to analyze a single file.",
            "files_scanned": len(py_files),
            "file_budget": _MAX_FILES_PER_RUN,
        }

    # Quick line count check (read first 1 byte to verify readability, count newlines)
    total_lines = 0
    for f in py_files:
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                total_lines += sum(1 for _ in fh)
        except OSError:
            continue
        if total_lines > _MAX_TOTAL_LINES:
            return {
                "error": "Line budget exceeded — use the `file` parameter to analyze a single file.",
                "lines_scanned": total_lines,
                "line_budget": _MAX_TOTAL_LINES,
            }

    return py_files


def _build_manifests(project_root: str, py_files: list[str]) -> tuple[Any, Any]:
    """Build property and test effectiveness manifests from source files."""
    from lintgate.linters.performance_checks.manifest import build_manifest
    from lintgate.linters.test_effectiveness.manifest import (
        build_test_effectiveness_manifest,
    )

    prop_manifest = build_manifest(project_root, py_files)
    teff_manifest = build_test_effectiveness_manifest(project_root, py_files)
    return prop_manifest, teff_manifest


def _build_ledger(
    project_root: str,
    py_files: list[str],
    prop_manifest: Any,
    teff_manifest: Any,
) -> Any:
    """Build a specification ledger from manifests."""
    from lintgate.specification.ledger import build_specification_ledger

    return build_specification_ledger(prop_manifest, teff_manifest, project_root, py_files=py_files)


def _filter_by_function(ledger: Any, function: str | None) -> dict[str, Any]:
    """Filter ledger functions by optional function name substring."""
    if not function:
        return ledger.functions
    return {k: v for k, v in ledger.functions.items() if function.lower() in k.lower()}


def _impl_spec_analyze(
    path: str,
    file: str | None,
    function: str | None,
    helpers: Any,
) -> dict[str, Any]:
    """Implementation for spec_analyze."""
    project_root = helpers["_validate_project_root"](path)
    result = _resolve_py_files(project_root, file)
    if isinstance(result, dict):
        return result
    py_files = result
    if not py_files:
        return {"error": "No Python files found"}

    prop_manifest, teff_manifest = _build_manifests(project_root, py_files)
    ledger = _build_ledger(project_root, py_files, prop_manifest, teff_manifest)

    matching = _filter_by_function(ledger, function)
    if not matching:
        msg = "No functions found"
        if function:
            msg += f" matching '{function}'"
        return {"note": msg}

    functions_out: dict[str, Any] = {}
    for key, fs in matching.items():
        functions_out[key] = {
            "sigma": fs.core.estimated_sigma,
            "sigma_confidence": round(fs.core.sigma_confidence, 3),
            "regime": fs.core.regime,
            "specification_level": round(fs.core.specification_level, 3),
            "phase": fs.core.phase,
            "is_pure": fs.core.is_pure,
            "risk_score": round(fs.risk.risk_score, 3),
            "priority_band": fs.risk.priority_band,
            "testability_score": round(fs.testability.testability_score, 3),
            "design_signals": {
                "boundary_points": fs.design_signals.boundary_points,
                "equivalence_partitions": fs.design_signals.equivalence_partitions,
                "decision_rule_count": fs.design_signals.decision_rule_count,
                "predicate_effect_links": fs.design_signals.predicate_effect_links,
            },
            "optimization_hints": fs.optimization_hints,
            "stop_criteria_met": fs.stop_criteria_met,
        }

    next_actions = [
        NextAction(
            tool="spec_prescribe",
            args={"path": path},
            reason="Get test prescriptions for under-specified functions",
        ),
        NextAction(
            tool="spec_gate_check",
            args={"path": path},
            reason="Check optimization gate status",
        ),
    ]

    return {
        "project": project_root,
        "total_functions": len(matching),
        "specification_coverage": round(ledger.specification_coverage, 3),
        "regime_distribution": ledger.regime_distribution,
        "risk_distribution": ledger.risk_distribution,
        "mean_testability": round(ledger.mean_testability, 3),
        "functions": functions_out,
        "next_actions": serialize_next_actions(next_actions),
    }


def _impl_spec_prescribe(
    path: str,
    function: str | None,
    max_prescriptions: int,
    regression_mode: bool,
    helpers: Any,
) -> dict[str, Any]:
    """Implementation for spec_prescribe."""
    from lintgate.specification.prescriptions import prescribe

    project_root = helpers["_validate_project_root"](path)
    result = _resolve_py_files(project_root, None)
    if isinstance(result, dict):
        return result
    py_files = result
    if not py_files:
        return {"error": "No Python files found"}

    prop_manifest, teff_manifest = _build_manifests(project_root, py_files)
    ledger = _build_ledger(project_root, py_files, prop_manifest, teff_manifest)

    matching = _filter_by_function(ledger, function)
    if not matching:
        msg = "No functions found"
        if function:
            msg += f" matching '{function}'"
        return {"note": msg}

    all_prescriptions: list[dict[str, Any]] = []
    for _key, fs in matching.items():
        rxs = prescribe(fs, max_prescriptions=max_prescriptions, regression_mode=regression_mode)
        for rx in rxs:
            all_prescriptions.append(
                {
                    "function": rx.function_key,
                    "kind": rx.prescription_kind,
                    "description": rx.description,
                    "info_gain": round(rx.estimated_info_gain, 3),
                    "priority_band": rx.priority_band,
                    "uncovered_dimension": rx.uncovered_dimension,
                    "suggested_assertion": rx.suggested_assertion,
                }
            )

    # Sort globally by priority band then info gain
    band_order = {"P0": 0, "P1": 1, "P2": 2}
    all_prescriptions.sort(key=lambda p: (band_order.get(p["priority_band"], 3), -p["info_gain"]))

    next_actions = [
        NextAction(
            tool="spec_analyze",
            args={"path": path},
            reason="View full specification analysis",
        ),
    ]

    return {
        "project": project_root,
        "total_prescriptions": len(all_prescriptions),
        "regression_mode": regression_mode,
        "prescriptions": all_prescriptions[:max_prescriptions],
        "next_actions": serialize_next_actions(next_actions),
    }


def _impl_spec_composition(
    path: str,
    module_a: str | None,
    module_b: str | None,
    helpers: Any,
) -> dict[str, Any]:
    """Implementation for spec_composition."""
    from lintgate.specification.call_graph import build_cross_module_call_graph
    from lintgate.specification.composition import analyze_composition

    project_root = helpers["_validate_project_root"](path)
    result = _resolve_py_files(project_root, None)
    if isinstance(result, dict):
        return result
    py_files = result
    if not py_files:
        return {"error": "No Python files found"}

    prop_manifest, teff_manifest = _build_manifests(project_root, py_files)
    ledger = _build_ledger(project_root, py_files, prop_manifest, teff_manifest)
    call_graph = build_cross_module_call_graph(py_files, project_root)

    result = analyze_composition(call_graph, ledger)
    output = result.to_dict()

    # Filter by module if requested
    if module_a or module_b:
        filtered_gaps = {}
        for edge_key, gap in output.get("composition_gaps", {}).items():
            parts = edge_key.split("::")
            if module_a and module_a not in parts[0]:
                continue
            if module_b and module_b not in edge_key:
                continue
            filtered_gaps[edge_key] = gap
        output["composition_gaps"] = filtered_gaps
        output["filter"] = {"module_a": module_a, "module_b": module_b}

    output["project"] = project_root
    return output


def _impl_spec_gate_check(
    path: str,
    function: str | None,
    hint: str | None,
    helpers: Any,
) -> dict[str, Any]:
    """Implementation for spec_gate_check."""
    from lintgate.specification.optimization_gate import check_gate

    project_root = helpers["_validate_project_root"](path)
    result = _resolve_py_files(project_root, None)
    if isinstance(result, dict):
        return result
    py_files = result
    if not py_files:
        return {"error": "No Python files found"}

    prop_manifest, teff_manifest = _build_manifests(project_root, py_files)
    ledger = _build_ledger(project_root, py_files, prop_manifest, teff_manifest)

    matching = _filter_by_function(ledger, function)
    # Filter to functions with optimization hints
    with_hints = {k: v for k, v in matching.items() if v.optimization_hints}
    if hint:
        with_hints = {k: v for k, v in with_hints.items() if hint in v.optimization_hints}

    if not with_hints:
        return {
            "note": "No functions with optimization hints found",
            "filter": {"function": function, "hint": hint},
        }

    gate_results: list[dict[str, Any]] = []
    for _key, fs in with_hints.items():
        gr = check_gate(fs)
        gate_results.append(
            {
                "function": gr.function_key,
                "passed": gr.passed,
                "stop_criteria_met": gr.stop_criteria_met,
                "spec_level": round(gr.spec_level, 3),
                "required_threshold": round(gr.required_threshold, 3),
                "delta": round(gr.delta, 3),
                "estimated_tests_remaining": gr.estimated_tests_remaining,
                "gated_hints": gr.gated_hints,
                "passed_hints": gr.passed_hints,
            }
        )

    passed_count = sum(1 for g in gate_results if g["passed"])
    stop_count = sum(1 for g in gate_results if g["stop_criteria_met"])

    next_actions = [
        NextAction(
            tool="spec_prescribe",
            args={"path": path},
            reason="Get prescriptions to close specification gaps",
        ),
    ]

    return {
        "project": project_root,
        "total_checked": len(gate_results),
        "passed": passed_count,
        "stop_criteria_met": stop_count,
        "gate_results": gate_results,
        "next_actions": serialize_next_actions(next_actions),
    }


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


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
        result = _impl_spec_analyze(path, file, function, helpers)
        return helpers["_json_dumps"](result, output_mode="compact")

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
        result = _impl_spec_prescribe(path, function, max_prescriptions, regression_mode, helpers)
        return helpers["_json_dumps"](result, output_mode="compact")

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
        result = _impl_spec_composition(path, module_a, module_b, helpers)
        return helpers["_json_dumps"](result, output_mode="compact")

    @mcp.tool()
    def spec_gate_check(
        path: str,
        function: str | None = None,
        hint: str | None = None,
    ) -> str:
        """Optimization gate validation with stop criteria.

        WHEN TO USE: To check whether optimization hints (cacheable,
        parallelizable, etc.) are backed by sufficient specification
        evidence. Shows which hints pass their thresholds, the delta
        to reach them, and estimated tests remaining.

        Example: spec_gate_check(path="/my/project")
        Example: spec_gate_check(path="/my/project", hint="cacheable")

        Args:
            path: Project root path.
            function: Optional function name to check.
            hint: Optional specific hint to filter by (e.g., "cacheable").
        """
        result = _impl_spec_gate_check(path, function, hint, helpers)
        return helpers["_json_dumps"](result, output_mode="compact")

    return {
        "spec_analyze": spec_analyze,
        "spec_prescribe": spec_prescribe,
        "spec_composition": spec_composition,
        "spec_gate_check": spec_gate_check,
    }
