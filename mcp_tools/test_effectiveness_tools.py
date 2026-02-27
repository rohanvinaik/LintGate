"""Test effectiveness tools — analyze_test_strength, inspect_test_assertions."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from lintgate.linters.test_effectiveness.calibration import (
    CALIBRATION_FILE,
    calibrate_weights,
    get_effective_weights,
    get_mutation_data_hash,
    save_calibration,
)
from lintgate.linters.test_effectiveness.test_effectiveness_logic import (
    AnalysisState,
    analyze_function_effectiveness,
    apply_filters,
    build_assertion_upgrades,
    build_manifest_for_project,
    build_summary,
    handle_no_mapped_functions,
    reconcile_with_coverage,
)


def _analyze_test_strength_impl(
    path: str,
    helpers: Any,
    file_filter: str | None = None,
    function_filter: str | None = None,
    max_runtime_ms: int | None = None,
) -> str:
    """Implementation of analyze_test_strength tool."""
    _start = time.perf_counter()

    project_root = helpers["_validate_project_root"](path)

    # Load calibrated weights if available
    survivors_path = os.path.join(project_root, "mutants", "mutmut-survivors.json")
    effective_weights = get_effective_weights(project_root, survivors_path)

    manifest, py_files, test_files, source_files = build_manifest_for_project(
        project_root, effective_weights=effective_weights
    )

    if not py_files:
        return json.dumps(
            {
                "error": "No Python files found in path.",
                "state": AnalysisState.NO_PYTHON_FILES.value,
            }
        )

    if not test_files:
        return json.dumps(
            {
                "note": "No test files found (looking for test_*.py).",
                "state": AnalysisState.NO_TEST_FILES.value,
            }
        )

    if not source_files:
        return json.dumps(
            {
                "note": "Test files found, but no source files found to analyze.",
                "hint": "Run this tool on the project root, not the tests directory.",
                "details": f"Found {len(test_files)} test files but 0 source files in '{path}'.",
                "state": "no_source_files",
            }
        )

    if manifest is None:
        return json.dumps(
            {
                "error": "Failed to build effectiveness manifest.",
                "state": "manifest_build_failed",
            }
        )

    if not manifest.functions:
        # (#56) Distinguish: were there any mappings at all but no public functions?
        diag = manifest.diagnostics
        if diag.mapped > 0:
            return json.dumps(
                {
                    "note": "Mappings were found but no analyzable public functions were produced.",
                    "hint": "Ensure source files contain public (non-underscore) functions.",
                    "state": AnalysisState.MAPPINGS_FOUND_BUT_NO_ANALYZABLE_PUBLIC_FUNCTIONS.value,
                    "diagnostics": diag.to_dict(),
                }
            )
        return handle_no_mapped_functions(manifest, source_files, test_files)

    # (#70) Check runtime budget — warn with confidence downgrade if analysis took too long
    elapsed_ms = (time.perf_counter() - _start) * 1000
    analysis_truncated = max_runtime_ms is not None and elapsed_ms > max_runtime_ms

    result = build_summary(manifest, project_root)

    if analysis_truncated:
        result["state"] = AnalysisState.ANALYSIS_TRUNCATED.value
        result["analysis_truncated"] = True
        result["scanned_source_files"] = len(source_files)
        result["scanned_test_files"] = len(test_files)
        result["elapsed_ms"] = round(elapsed_ms, 1)
        result["confidence"] = "low"  # downgrade confidence on truncated analysis
        result["truncation_note"] = (
            f"Analysis exceeded {max_runtime_ms}ms budget. Results may be incomplete. "
            "Consider scoping with file_filter or increasing max_runtime_ms."
        )
    else:
        result["state"] = AnalysisState.SUCCESS.value
        result["analysis_truncated"] = False

    # Check for calibration staleness
    cal_path = os.path.join(project_root, CALIBRATION_FILE)
    if os.path.exists(cal_path):
        current_hash = get_mutation_data_hash(survivors_path)
        with open(cal_path) as f:
            stored_hash = json.load(f).get("source_hash")
        if stored_hash != current_hash:
            result["calibration_stale"] = True
            result["calibration_note"] = (
                "Calibration stale. Underlying mutation data has changed. Run calibrate_assertion_weights(path) to refresh."
            )
        else:
            result["calibration_stale"] = False
    else:
        result["calibration_stale"] = None  # Never calibrated

    apply_filters(result, file_filter, function_filter)

    result["assertion_upgrades"] = build_assertion_upgrades(manifest)

    # (#88) Reconciliation Report
    coverage_path = os.path.join(project_root, "coverage.json")
    if os.path.exists(coverage_path):
        try:
            with open(coverage_path) as f:
                coverage_data = json.load(f)
            result["reconciliation_report"] = reconcile_with_coverage(manifest, coverage_data)
        except (json.JSONDecodeError, OSError):
            result["reconciliation_report"] = {"error": "Failed to parse coverage.json"}
    else:
        result["reconciliation_report"] = {
            "note": "coverage.json not found. Run pytest --cov --cov-report=json to enable reconciliation."
        }

    from lintgate.mutation.ci_stats import MutationCIStats, load_mutation_hotspots

    stats_path = os.path.join(project_root, "mutants", "mutmut-cicd-stats.json")
    survivors_path = os.path.join(project_root, "mutants", "mutmut-survivors.json")

    result["mutation_ci_context"] = MutationCIStats.from_json_path(stats_path).to_dict()
    result["mutation_hotspots"] = load_mutation_hotspots(survivors_path)

    result["next_actions"] = [
        "inspect_test_assertions(path, test_file) — drill into specific test file",
        "controlplane_test_skeleton(source_file) — generate mutation-aware test stubs",
        "generate_property_tests(path) — Hypothesis templates for pure functions",
    ]

    return helpers["_json_dumps"](result, output_mode="compact")


def _inspect_test_assertions_impl(path: str, test_file: str, helpers: Any) -> str:
    """Implementation of inspect_test_assertions tool."""
    from lintgate.linters.test_effectiveness.types import (
        TEFF_SCHEMA_VERSION,
    )

    project_root = helpers["_validate_project_root"](path)
    target_files = _resolve_target_files(project_root, test_file)

    if isinstance(target_files, dict) and "error" in target_files:
        return json.dumps(target_files)

    if not target_files:
        return json.dumps({"note": "No test files found to inspect."})

    target_files.sort()

    result: dict[str, Any] = {
        "project_root": project_root,
        "schema_version": TEFF_SCHEMA_VERSION,
        "test_files_analyzed": len(target_files),
        "test_functions": {},
        "summary": {
            "total_assertions": 0,
            "semantic_assertions": 0,
            "structural_assertions": 0,
            "effectiveness_score": 0.0,
            "quality_profile": {},
        },
        "file_errors": {},
        "contract_test_anti_patterns": [],
    }

    _process_test_files(target_files, project_root, result)
    _truncate_results(result)
    _compute_test_summary(result, target_files)

    from lintgate.mutation.ci_stats import load_mutation_hotspots

    survivors_path = os.path.join(project_root, "mutants", "mutmut-survivors.json")
    all_hotspots = load_mutation_hotspots(survivors_path)

    # Filter hotspots to only include files we analyzed
    analyzed_relpaths = {os.path.relpath(f, project_root) for f in target_files}
    result["mutation_hotspots"] = [h for h in all_hotspots if h.get("file") in analyzed_relpaths]

    return helpers["_json_dumps"](result)


def _resolve_target_files(project_root: str, test_file: str) -> list[str] | dict[str, str]:
    """Resolve target test files for inspection."""
    from lintgate.linters.test_effectiveness.test_analyzer import _discover_test_files

    if not test_file:
        return _discover_test_files(project_root)

    full_test_path = (
        test_file if os.path.isabs(test_file) else os.path.join(project_root, test_file)
    )
    if os.path.isdir(full_test_path):
        return _discover_test_files(full_test_path)
    elif os.path.exists(full_test_path):
        return [full_test_path]
    return {"error": f"Test file/directory not found: {test_file}"}


def _process_test_files(target_files: list[str], project_root: str, result: dict[str, Any]) -> None:
    """Process a list of test files and update the result dict."""
    from lintgate.linters.test_effectiveness.assertion_classifier import (
        classify_test_file_from_path,
    )

    for t_file in target_files:
        try:
            file_assertions = classify_test_file_from_path(t_file)
            if not file_assertions:
                continue

            rel_path = os.path.relpath(t_file, project_root)

            for func_name, assertions in file_assertions.items():
                # Qualify name if in batch mode
                full_func_name = f"{rel_path}::{func_name}" if len(target_files) > 1 else func_name

                func_data, anti_patterns = analyze_function_effectiveness(func_name, assertions)
                result["contract_test_anti_patterns"].extend(
                    {**ap, "function": full_func_name} for ap in anti_patterns
                )

                result["test_functions"][full_func_name] = func_data
                result["summary"]["total_assertions"] += func_data["count"]
                result["summary"]["semantic_assertions"] += func_data["semantic_count"]
                result["summary"]["structural_assertions"] += func_data["structural_count"]

        except Exception as e:
            result["file_errors"][t_file] = {
                "error_kind": type(e).__name__,
                "message": str(e),
                "file": t_file,
            }


def _truncate_results(result: dict[str, Any], max_funcs: int = 50) -> None:
    """Truncate test functions if they exceed the maximum limit."""
    if len(result["test_functions"]) > max_funcs:
        all_sorted_keys = sorted(result["test_functions"].keys())
        truncated_keys = all_sorted_keys[:max_funcs]
        result["test_functions"] = {k: result["test_functions"][k] for k in truncated_keys}
        result["summary"]["note"] = (
            f"Results truncated to top {max_funcs} functions for performance. Use file_filter to narrow scope."
        )
        result["truncated"] = True
    else:
        result["truncated"] = False


def _compute_test_summary(result: dict[str, Any], target_files: list[str]) -> None:
    """Compute summary metrics for test assertions."""
    _compute_summary_metadata(result, target_files)
    _compute_quality_profile(result)


def _compute_summary_metadata(result: dict[str, Any], target_files: list[str]) -> None:
    """Compute basic metadata counts and ratios for the summary."""
    analyzed_func_count = len(result["test_functions"])
    analyzed_file_count = len(target_files) - len(result["file_errors"])
    total_file_count = len(target_files)

    # (#84) sentinel_ratio: fraction of analyzed functions with an isolated sentinel
    isolated_count = sum(
        1 for f in result["test_functions"].values() if f.get("has_isolated_sentinel")
    )
    result["summary"]["sentinel_ratio"] = (
        round(isolated_count / analyzed_func_count, 3) if analyzed_func_count > 0 else 0.0
    )

    # (#85) file count metadata
    result["summary"]["analyzed_file_count"] = analyzed_file_count
    result["summary"]["total_file_count"] = total_file_count
    if analyzed_file_count < total_file_count:
        result["summary"]["ratio_note"] = (
            f"quality_profile computed over {analyzed_file_count}/{total_file_count} files; "
            f"{total_file_count - analyzed_file_count} errored."
        )


def _compute_quality_profile(result: dict[str, Any]) -> None:
    """Compute average effectiveness score and semantic/structural ratios."""
    from lintgate.linters.test_effectiveness.types import (
        AssertionInfo,
        FunctionEffectiveness,
    )

    if not result["test_functions"]:
        return

    total = result["summary"]["total_assertions"]
    func_list = [
        FunctionEffectiveness(
            function_name=f_name,
            assertions=[AssertionInfo.from_dict(ai) for ai in f_data["assertions"]],
        )
        for f_name, f_data in result["test_functions"].items()
    ]

    if func_list:
        avg_score = sum(f.compute_scores() or f.effectiveness_score for f in func_list) / len(
            func_list
        )
        result["summary"]["effectiveness_score"] = round(avg_score, 3)

        total_sem = result["summary"]["semantic_assertions"]
        result["summary"]["quality_profile"] = {
            "semantic_ratio": round(total_sem / total, 3) if total > 0 else 0.0,
            "structural_ratio": round(result["summary"]["structural_assertions"] / total, 3)
            if total > 0
            else 0.0,
        }


def register(mcp: Any, helpers: Any) -> dict[str, Any]:
    """Register test effectiveness tools on the shared MCP instance."""

    @mcp.tool()
    def analyze_test_strength(
        path: str,
        file_filter: str | None = None,
        function_filter: str | None = None,
        max_runtime_ms: int | None = None,
    ) -> str:
        """Analyze test assertion quality and mutation vulnerability for a project.

        Returns project summary, top vulnerable functions, and suggested assertion upgrades.
        Optional max_runtime_ms budget: if exceeded, returns a partial result with
        state='analysis_truncated' and confidence='low'.
        """
        return _analyze_test_strength_impl(
            path, helpers, file_filter, function_filter, max_runtime_ms
        )

    @mcp.tool()
    def inspect_test_assertions(
        path: str,
        test_file: str,
    ) -> str:
        """Drill down into a single test file showing every assertion classified.

        Returns assertion list with kind, target, line, and mutation score.
        """
        return _inspect_test_assertions_impl(path, test_file, helpers)

    @mcp.tool()
    def calibrate_assertion_weights(path: str) -> str:
        """Run the mutation-backed calibration pipeline to adjust assertion weights.

        WHEN TO USE: After mutation testing (mutmut) has been run and you want
        to provide evidence-based weighting for assertion kinds. Persists results
        to .lintgate/calibration.json with a hash for staleness detection.
        """
        project_root = helpers["_validate_project_root"](path)
        survivors_path = os.path.join(project_root, "mutants", "mutmut-survivors.json")

        if not os.path.exists(survivors_path):
            return json.dumps(
                {
                    "error": "Mutation survivors file not found at mutants/mutmut-survivors.json",
                    "hint": "Run mutation testing first.",
                }
            )

        manifest, py_files, test_files, _ = build_manifest_for_project(project_root)
        if not manifest or not manifest.functions:
            return json.dumps({"error": "No functions found to calibrate against."})

        weights = calibrate_weights(project_root, survivors_path, manifest)
        source_hash = get_mutation_data_hash(survivors_path)
        save_calibration(project_root, weights, source_hash)

        return json.dumps(
            {
                "status": "success",
                "message": "Assertion weights calibrated and saved.",
                "source_hash": source_hash,
                "weights_changed": len(weights),
            }
        )

    return {
        "analyze_test_strength": analyze_test_strength,
        "inspect_test_assertions": inspect_test_assertions,
        "calibrate_assertion_weights": calibrate_assertion_weights,
    }
