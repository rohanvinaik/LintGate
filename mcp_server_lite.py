"""LintGate MCP Server — Lite surface (17 tools).

Covers the full golden-path workflow:
  1. Orient & fix:     check_project → get_details → apply_repairs / fix_lint
  2. Guard rails:      after_edit, before_commit
  3. Measure:          spec_analyze (σ per function)
  4. Profile & test:   improve_tests → generate_tests → converge → apply
  5. Decompose:        decompose (mutation-driven) → simplify (AST extraction)
  6. Housekeeping:     reset_state (clear stale mutation data)

Internal tools are chained on CPU by the high-level tools.
next_actions in responses are filtered to only reference tools on this surface.

Schema: ~2K tokens vs ~21K for the full server.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "LintGate",
    instructions=(
        "Specification compiler for Python. "
        "Start: check_project(path). Drill in: get_details(run_id). "
        "After edits: after_edit(path). Before commit: before_commit(path). "
        "Measure: spec_analyze(path, file). Profile: improve_tests(path, file). "
        "Test skeletons: generate_tests(path, file). "
        "Converge: converge(path, file). Apply: apply(path, workflow_id). "
        "Decompose: decompose(path, file). Reset: reset_state(path). "
        "Optimize: triage_tests(path, file). Compact: compact_tests(path, file)."
    ),
)

# Import the full machinery (but don't expose it all as tools)
from mcp_server import _tool_funcs


# =====================================================================
# next_actions filtering — rewrite full-server names to lite names
# =====================================================================

# Reverse mapping: full-server tool name → lite tool name.
# Tools not in this map are dropped from next_actions.
_FULL_TO_LITE = {
    "getting_started": "getting_started",
    "controlplane_run": "check_project",
    "controlplane_get_details": "get_details",
    "lint_fix": "fix_lint",
    "controlplane_apply_repairs": "apply_repairs",
    "after_edit": "after_edit",
    "before_commit": "before_commit",
    "spec_file_analyze": "spec_analyze",
    "mutation_run_full": "improve_tests",
    "mutation_prescribe": "improve_tests",
    "platonic_converge": "converge",
    "platonic_apply": "apply",
    "platonic_continue": "converge",
    "mutation_decompose": "decompose",
    "refactor_extract_method": "simplify",
    "mutation_clear_state": "reset_state",
    "mutation_prescribe_tests": "generate_tests",
    "test_suite_triage": "triage_tests",
    "test_suite_compact": "compact_tests",
    "lint_files": "after_edit",
    "lint_project": "check_project",
}


def _filter(response_json: str) -> str:
    """Rewrite next_actions to only reference lite-surface tools."""
    try:
        data = json.loads(response_json)
    except (json.JSONDecodeError, TypeError):
        return response_json
    if not isinstance(data, dict):
        return response_json
    actions = data.get("next_actions")
    if not actions or not isinstance(actions, list):
        return response_json
    filtered = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        lite_name = _FULL_TO_LITE.get(a.get("tool", ""))
        if lite_name is not None:
            filtered.append({**a, "tool": lite_name})
    data["next_actions"] = filtered
    return json.dumps(data, separators=(",", ":"), default=str)


# =====================================================================
# Orient & assess
# =====================================================================


@mcp.tool()
def getting_started(path: str) -> str:
    """Set up and orient on a project. Call this first.

    Auto-creates config, checks environment, returns action plan.
    """
    return _filter(_tool_funcs["getting_started"](path))


@mcp.tool()
def check_project(path: str) -> str:
    """Run a full project health check. Returns top blockers and next actions.

    Runs lint, tests, deps, git, and structure analysis in parallel.
    Use get_details(run_id) to drill into findings.
    """
    return _filter(
        _tool_funcs["controlplane_run"](path=path, scope="changed", strictness="relaxed")
    )


@mcp.tool()
def get_details(
    run_id: str,
    severity: str | None = None,
    channel: str | None = None,
    max_issues: int = 10,
    finding_domain: str | None = None,
) -> str:
    """Drill into findings from a check_project run.

    Args:
        run_id: The run_id from check_project output.
        severity: Filter by severity — blocking, warning, informational.
        channel: Filter by channel — lint, tests, deps, git, behavior, structure.
        max_issues: Maximum findings to return (default 10).
        finding_domain: "code" excludes dependency CVEs, "environment" for the inverse.
    """
    return _filter(_tool_funcs["controlplane_get_details"](
        run_id=run_id, channel=channel, severity=severity,
        max_issues=max_issues, finding_domain=finding_domain,
    ))


# =====================================================================
# Fix
# =====================================================================


@mcp.tool()
def fix_lint(path: str) -> str:
    """Auto-fix all safe lint issues.

    Applies ruff safe fixes (formatting, import sorting, simple corrections).
    """
    return _filter(_tool_funcs["lint_fix"](path=path, dry_run=False))


@mcp.tool()
def apply_repairs(path: str, run_id: str | None = None, safe_only: bool = True) -> str:
    """Apply safe repairs from a check_project run.

    Args:
        path: Project root.
        run_id: Run ID from check_project (uses latest if not specified).
        safe_only: Only execute safe repairs (default True).
    """
    return _filter(_tool_funcs["controlplane_apply_repairs"](
        path=path, action_ids=None, safe_only=safe_only, run_id=run_id,
    ))


# =====================================================================
# Guard rails
# =====================================================================


@mcp.tool()
def after_edit(path: str, files: list[str] | None = None) -> str:
    """Run after editing files. Fast lint check on changed files.

    Args:
        path: Project root.
        files: Files edited. Auto-detects from git if not specified.
    """
    return _filter(_tool_funcs["after_edit"](path=path, files=files))


@mcp.tool()
def before_commit(path: str) -> str:
    """Run before committing. Checks lint + secrets on staged files."""
    return _filter(_tool_funcs["before_commit"](path=path))


# =====================================================================
# Measure
# =====================================================================


@mcp.tool()
def spec_analyze(path: str, file: str) -> str:
    """Specification complexity (σ) per function in a file.

    Returns σ, regime, phase, risk score, and design signals per function.
    High σ means the function needs decomposition, not more tests.

    Args:
        path: Project root.
        file: Python file to analyze (e.g., "src/core.py").
    """
    return _filter(_tool_funcs["spec_file_analyze"](path=path, file=file, enrich=True))


# =====================================================================
# Specify & validate
# =====================================================================


@mcp.tool()
def improve_tests(path: str, file: str) -> str:
    """Mutation-test a file: actual kill rates + prescriptions.

    Runs exhaustive mutation profiling then prescribes improvements
    for surviving categories. Use generate_tests() for test skeletons.

    Args:
        path: Project root.
        file: Python file to analyze (e.g., "src/core.py").
    """
    full_result = _tool_funcs["mutation_run_full"](path=path, file=file)
    result = json.loads(full_result)

    if "analysis_id" in result:
        prescribe_result = _tool_funcs["mutation_prescribe"](path=path, file=file)
        prescribe_data = json.loads(prescribe_result)
        combined_summary = (
            result.get("summary", "") + "\n\n" + prescribe_data.get("summary", "")
        )
        return _filter(json.dumps({
            "analysis_id": result["analysis_id"],
            "summary": combined_summary,
            "file": result.get("file", ""),
            "profiling_id": result["analysis_id"],
            "prescription_id": prescribe_data.get("analysis_id", ""),
            "queryable_sections": result.get("queryable_sections", []),
        }))

    return _filter(full_result)


@mcp.tool()
def generate_tests(path: str, file: str = "", function: str | None = None) -> str:
    """Generate pytest skeletons targeting surviving mutation categories.

    Creates test templates from mutation profiles. Run after improve_tests
    identifies survivors. Tests may need oracle values filled in.

    Args:
        path: Project root.
        file: Source file.
        function: Optional specific function name.
    """
    return _filter(
        _tool_funcs["mutation_prescribe_tests"](path=path, file=file, function=function)
    )


@mcp.tool()
def converge(
    path: str,
    file: str,
    max_iterations: int = 5,
    decompose_mode: str = "propose",
) -> str:
    """Full specification convergence: profile → generate tests → validate → iterate.

    The golden path. Runs the entire spec-mutation pipeline on CPU in a
    single call. Non-destructive. Use apply() to promote validated results.

    Args:
        path: Project root.
        file: Python file (e.g., "src/core.py").
        max_iterations: Maximum convergence iterations (default 5).
        decompose_mode: "propose" (flag for manual decomposition),
            "skip" (bypass entanglement check), or "auto" (include
            extraction plan in response).
    """
    return _filter(_tool_funcs["platonic_converge"](
        path=path, file=file, max_iterations=max_iterations,
        decompose_mode=decompose_mode,
    ))


@mcp.tool()
def apply(path: str, workflow_id: str, dry_run: bool = True) -> str:
    """Apply validated results from converge to the live test suite.

    Dry-run by default — preview before committing.
    Allows dry-run preview even in NEEDS_DECOMPOSITION state.

    Args:
        path: Project root.
        workflow_id: Workflow ID returned by converge.
        dry_run: Preview actions without executing (default True).
    """
    return _filter(_tool_funcs["platonic_apply"](
        path=path, workflow_id=workflow_id, dry_run=dry_run,
    ))


# =====================================================================
# Decompose & refactor
# =====================================================================


@mcp.tool()
def decompose(path: str, file: str, function: str | None = None) -> str:
    """Find entangled functions from mutation survival profiles.

    Identifies functions where multiple mutation categories survive.
    Use simplify() to execute the prescribed extraction.

    Args:
        path: Project root.
        file: Python file to analyze.
        function: Optional specific function name.
    """
    return _filter(_tool_funcs["mutation_decompose"](
        path=path, file=file, function=function, mode="auto",
    ))


@mcp.tool()
def simplify(
    path: str,
    file: str,
    start_line: int,
    end_line: int,
    helper_name: str,
    dry_run: bool = True,
) -> str:
    """Extract a block of code into a helper function.

    Analyzes variable flow (inputs, outputs, closures) and generates
    the extraction. Use dry_run=True first to preview.

    Args:
        path: Project root.
        file: File containing the block.
        start_line: First line to extract.
        end_line: Last line to extract.
        helper_name: Name for the new helper function.
        dry_run: Preview only (default True). Set False to apply.
    """
    return _filter(_tool_funcs["refactor_extract_method"](
        path=path, file=file, start_line=start_line,
        end_line=end_line, helper_name=helper_name, dry_run=dry_run,
    ))


# =====================================================================
# Housekeeping
# =====================================================================


@mcp.tool()
def reset_state(path: str, file: str | None = None) -> str:
    """Clear stale mutation/convergence state.

    Use after: fixing import errors, adding test files, restructuring code.
    Without file arg, clears all state for the project.

    Args:
        path: Project root.
        file: Optional file to scope the clear (clears all if omitted).
    """
    return _filter(_tool_funcs["mutation_clear_state"](path=path, file=file))


# =====================================================================
# Test optimization
# =====================================================================


@mcp.tool()
def triage_tests(path: str, file: str) -> str:
    """Diagnose test redundancy via mutation convergence analysis.

    Extracts the minimum killing set — smallest subset of tests that
    achieves the same kill rate. Requires prior improve_tests run.

    Args:
        path: Project root.
        file: Source file to triage tests for.
    """
    return _filter(_tool_funcs["test_suite_triage"](path=path, file=file))


@mcp.tool()
def compact_tests(path: str, file: str, dry_run: bool = True) -> str:
    """Compact a test file to its minimum killing set.

    AST-extracts only tests that contribute unique mutation kills.
    Preserves fixtures, helpers, imports. Dry-run by default.

    Args:
        path: Project root.
        file: Source file whose tests to compact.
        dry_run: Preview without writing (default True).
    """
    return _filter(_tool_funcs["test_suite_compact"](path=path, file=file, dry_run=dry_run))


def run_server() -> None:
    mcp.run()


if __name__ == "__main__":
    run_server()
