"""Specification tools — shared helpers and implementation functions.

Extracted from specification_tools.py for file-size compliance.
The public API remains specification_tools.register().
"""

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


# ── Path validation ───────────────────────────────────────────────


def validate_file_in_project(project_root: str, file: str) -> str:
    """Resolve *file* to an absolute path and verify it lives inside *project_root*.

    Resolves symlinks and ``..`` components via ``os.path.realpath`` so that
    path-traversal tricks like ``../../etc/passwd`` are caught.

    Returns the resolved absolute path on success.
    Raises ``ValueError`` if the resolved path escapes *project_root*.
    """
    full = os.path.join(project_root, file) if not os.path.isabs(file) else file
    resolved = os.path.realpath(full)
    root_resolved = os.path.realpath(project_root)
    if not resolved.startswith(root_resolved + os.sep) and resolved != root_resolved:
        raise ValueError(f"File path escapes project root: {file!r} resolves to {resolved}")
    return resolved


def resolve_py_files(project_root: str, file: str | None) -> list[str] | dict[str, Any]:
    """Resolve file list: single file if specified, canonical discovery otherwise.

    Returns a list of paths on success, or an error dict on failure.
    """
    if file:
        try:
            full = validate_file_in_project(project_root, file)
        except ValueError as exc:
            return {"error": str(exc)}
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


# ── Manifest / ledger building ────────────────────────────────────


def load_mutation_cache(project_root: str) -> dict[str, dict[str, Any]] | None:
    """Load all mutation cache entries for spec_level override."""
    import json
    from pathlib import Path

    cache_dir = Path(project_root) / ".lintgate" / "mutation"
    if not cache_dir.exists():
        return None

    cache: dict[str, dict[str, Any]] = {}
    for cache_file in cache_dir.glob("*.json"):
        if cache_file.name == "scheduler_state.json":
            continue
        try:
            with open(cache_file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        func_key = data.get("function_key", "")
        if func_key:
            cache[func_key] = data

    return cache if cache else None


def build_manifests(
    project_root: str,
    py_files: list[str],
    mutation_cache: dict[str, dict[str, Any]] | None = None,
) -> tuple[Any, Any]:
    """Build property and test effectiveness manifests from source files."""
    from lintgate.linters.performance_checks.manifest import build_manifest
    from lintgate.linters.test_effectiveness.manifest import (
        build_test_effectiveness_manifest,
    )

    prop_manifest = build_manifest(project_root, py_files, mutation_cache=mutation_cache)
    teff_manifest = build_test_effectiveness_manifest(project_root, py_files)
    return prop_manifest, teff_manifest


def build_call_graph(project_root: str, py_files: list[str]) -> Any:
    """Build cross-module call graph for fan-in/fan-out scoring."""
    from lintgate.specification.call_graph import build_cross_module_call_graph

    return build_cross_module_call_graph(py_files, project_root)


def build_ledger(
    project_root: str,
    py_files: list[str],
    prop_manifest: Any,
    teff_manifest: Any,
    call_graph: Any = None,
    mutation_cache: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Build a specification ledger from manifests."""
    from lintgate.specification.ledger import build_specification_ledger

    return build_specification_ledger(
        prop_manifest,
        teff_manifest,
        project_root,
        py_files=py_files,
        call_graph=call_graph,
        mutation_cache=mutation_cache,
    )


def filter_by_function(ledger: Any, function: str | None) -> dict[str, Any]:
    """Filter ledger functions by optional function name substring."""
    if not function:
        return dict(ledger.functions)
    return {k: v for k, v in ledger.functions.items() if function.lower() in k.lower()}


def resolve_and_build_ledger(
    helpers: Any,
    path: str,
    file: str | None,
) -> tuple[str, list[str], Any, dict[str, dict[str, Any]] | None] | dict[str, Any]:
    """Common setup: validate root, resolve files, build ledger.

    Returns (project_root, py_files, ledger, mutation_cache) on success,
    or an error dict on failure.
    """
    project_root: str = helpers["_validate_project_root"](path)
    result = resolve_py_files(project_root, file)
    if isinstance(result, dict):
        return result
    py_files: list[str] = result
    if not py_files:
        return {"error": "No Python files found"}

    mutation_cache = load_mutation_cache(project_root)
    prop_manifest, teff_manifest = build_manifests(project_root, py_files, mutation_cache)
    call_graph = build_call_graph(project_root, py_files)
    ledger = build_ledger(
        project_root, py_files, prop_manifest, teff_manifest, call_graph, mutation_cache
    )
    return project_root, py_files, ledger, mutation_cache


# ── Implementation functions ──────────────────────────────────────


def impl_spec_analyze(
    path: str,
    file: str | None,
    function: str | None,
    helpers: Any,
) -> dict[str, Any]:
    """Implementation for spec_analyze."""
    setup = resolve_and_build_ledger(helpers, path, file)
    if isinstance(setup, dict):
        return setup
    project_root, _py_files, ledger, _mc = setup

    matching = filter_by_function(ledger, function)
    if not matching:
        msg = "No functions found"
        if function:
            msg += f" matching '{function}'"
        return {"note": msg}

    functions_out = _format_functions(matching)

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


def _format_functions(matching: dict[str, Any]) -> dict[str, Any]:
    """Format ledger function specs into output dicts."""
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
    return functions_out


def impl_spec_prescribe(
    path: str,
    function: str | None,
    max_prescriptions: int,
    regression_mode: bool,
    helpers: Any,
) -> dict[str, Any]:
    """Implementation for spec_prescribe."""
    from lintgate.specification.prescriptions import prescribe

    setup = resolve_and_build_ledger(helpers, path, None)
    if isinstance(setup, dict):
        return setup
    project_root, _py_files, ledger, _mc = setup

    matching = filter_by_function(ledger, function)
    if not matching:
        msg = "No functions found"
        if function:
            msg += f" matching '{function}'"
        return {"note": msg}

    all_prescriptions = _collect_prescriptions(
        matching, max_prescriptions, regression_mode, prescribe
    )

    next_actions = [
        NextAction(
            tool="mutation_run_sampling",
            args={"path": path, "file": "<target_file>"},
            reason="Empirically verify specification gaps via mutation analysis",
        ),
        NextAction(
            tool="spec_analyze",
            args={"path": path},
            reason="View full specification analysis",
        ),
        NextAction(
            tool="generate_property_tests",
            args={"path": path},
            reason="Generate Hypothesis property tests for pure functions",
            condition="if any prescribed functions are pure",
        ),
    ]

    return {
        "project": project_root,
        "total_prescriptions": len(all_prescriptions),
        "regression_mode": regression_mode,
        "prescriptions": all_prescriptions[:max_prescriptions],
        "next_actions": serialize_next_actions(next_actions),
    }


def _collect_prescriptions(
    matching: dict[str, Any],
    max_prescriptions: int,
    regression_mode: bool,
    prescribe_fn: Any,
) -> list[dict[str, Any]]:
    """Collect and sort prescriptions from matching functions."""
    all_prescriptions: list[dict[str, Any]] = []
    for _key, fs in matching.items():
        rxs = prescribe_fn(fs, max_prescriptions=max_prescriptions, regression_mode=regression_mode)
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

    band_order = {"P0": 0, "P1": 1, "P2": 2}
    all_prescriptions.sort(key=lambda p: (band_order.get(p["priority_band"], 3), -p["info_gain"]))
    return all_prescriptions


def impl_spec_composition(
    path: str,
    module_a: str | None,
    module_b: str | None,
    helpers: Any,
) -> dict[str, Any]:
    """Implementation for spec_composition."""
    from lintgate.specification.composition import analyze_composition

    setup = resolve_and_build_ledger(helpers, path, None)
    if isinstance(setup, dict):
        return setup
    project_root, _py_files, ledger, _mc = setup

    # Need call graph for composition — rebuild it
    result_files = resolve_py_files(project_root, None)
    if isinstance(result_files, dict):
        return result_files
    cg = build_call_graph(project_root, result_files)

    composition = analyze_composition(cg, ledger)
    output: dict[str, Any] = composition.to_dict()

    if module_a or module_b:
        filtered_gaps: dict[str, Any] = {}
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


def impl_spec_gate_check(
    path: str,
    file: str | None,
    function: str | None,
    hint: str | None,
    helpers: Any,
) -> dict[str, Any]:
    """Implementation for spec_gate_check."""
    from lintgate.specification.optimization_gate import check_gate

    setup = resolve_and_build_ledger(helpers, path, file)
    if isinstance(setup, dict):
        return setup
    project_root, _py_files, ledger, _mc = setup

    matching = filter_by_function(ledger, function)
    with_hints = {k: v for k, v in matching.items() if v.optimization_hints}
    if hint:
        with_hints = {k: v for k, v in with_hints.items() if hint in v.optimization_hints}

    if not with_hints:
        return {
            "note": "No functions with optimization hints found",
            "filter": {"function": function, "hint": hint},
        }

    gate_results = _evaluate_gates(with_hints, check_gate)

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


def _evaluate_gates(
    with_hints: dict[str, Any],
    check_gate_fn: Any,
) -> list[dict[str, Any]]:
    """Evaluate gate results for functions with optimization hints."""
    gate_results: list[dict[str, Any]] = []
    for _key, fs in with_hints.items():
        gr = check_gate_fn(fs)
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
    return gate_results
