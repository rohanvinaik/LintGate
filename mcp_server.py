#!/usr/bin/env python3
"""LintGate MCP Server — manual lint invocation for Claude Code.

Exposes LintGate as structured MCP tools so the agent (or user) can
trigger linting explicitly, not just through the PostToolUse hook.

Tool functions are registered by domain modules under mcp_tools/.
This file contains the FastMCP instance, shared constants, helper
functions, and the entry point.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Error: mcp package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

try:
    from lintgate.agent_reporter import format_report
    from lintgate.config import load_config
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import (
        generate_run_id,
        load_last_run,
        log_metric,
        save_run,
        save_run_details,
        update_issue_memory,
    )
    from lintgate.types import LintIssue, LintTier
except ModuleNotFoundError:
    _LINTGATE_DIR = Path(__file__).resolve().parent
    if str(_LINTGATE_DIR) not in sys.path:
        sys.path.insert(0, str(_LINTGATE_DIR))
    from lintgate.agent_reporter import format_report
    from lintgate.config import load_config
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import (
        generate_run_id,
        load_last_run,
        log_metric,
        save_run,
        save_run_details,
        update_issue_memory,
    )
    from lintgate.types import LintIssue, LintTier

_MCP_INSTRUCTIONS = (
    "LintGate: code quality analysis for Python projects. "
    "Start with getting_started(path) for project-specific guidance and startup auto-setup.\n"
    "Essential workflow (6 core tools):\n"
    "  1. lint_files — lint files you just edited\n"
    "  2. lint_project — full project lint scan\n"
    "  3. lint_fix — auto-fix safe issues\n"
    "  4. controlplane_run — comprehensive project health check "
    "(lint + tests + deps + git; works without config)\n"
    "  5. controlplane_get_details — drill into health check findings\n"
    "  6. bootstrap_context_files — generate project-specific CLAUDE.md\n"
    "Project compass (4-axis project understanding):\n"
    "  7. compass_status — show axis depths, gaps, staleness, and mode\n"
    "  8. compass_update — extract compass from project docs, optionally render context files\n"
    "  9. compass_interview — fill sparse axes with answers or code inference\n"
    "  10. compass_check — check an action against toward/away/forbidden directives\n"
    "  11. theory_mode_enter — enter theory exploration mode\n"
    "  12. theory_mode_freeze — freeze compass and exit to normal mode\n"
    "First session: controlplane_run(path) → controlplane_get_details(run_id) → "
    "lint_fix → bootstrap_context_files(path, write=true).\n"
    "Compass workflow: compass_update(path, write=true) → compass_interview(path) → "
    "compass_update(path, targets=['all'], write=true).\n"
    "Auto-improve workflow (platonic golden path — use this to improve any codebase):\n"
    "  Step 1: platonic_project(path) — selects the highest-value target file automatically\n"
    "  Step 2: Follow primary_next_action from the response (profiles, generates tests, validates)\n"
    "  Step 3: platonic_continue(path, workflow_id) — resume if interrupted\n"
    "  Step 4: platonic_apply(path, workflow_id) — apply when state is READY_TO_APPLY\n"
    "  Step 5: Repeat from Step 1 for the next file — each run picks the next best target\n"
    "  Alternative: platonic_converge(path, file) — if you already know which file to improve\n"
    "For a full codebase sweep: loop platonic_project → follow actions → platonic_apply → repeat.\n"
    "Decomposition workflow: mutation_decompose → extraction_plan → refactor_move (safe module move with import rewriting, dry-run default).\n"
    "Prescriptive spec workflow (specification-first code generation):\n"
    "  prescriptive_spec_compose(path, target) — compose behavioral contract from theory + compass\n"
    "  prescriptive_spec_compile(path, target) — compile contract into test skeletons + generation constraints\n"
    "  [write code guided by generation_prompt in compile output]\n"
    "  prescriptive_spec_verify(path, file) — verify refinement (structural AST checks + behavioral mutation checks)\n"
    "  prescriptive_spec_status(path) — project-wide prescriptive coverage\n"
    "All responses include next_actions with suggested follow-up tools. "
    "127 tools total — use getting_started or lint_status to explore."
)


def _build_mcp_server() -> FastMCP:
    """Instantiate FastMCP across mcp package versions."""
    try:
        sig = inspect.signature(FastMCP.__init__)
        if "instructions" in sig.parameters:
            return FastMCP("LintGate", instructions=_MCP_INSTRUCTIONS)
        if "description" in sig.parameters:
            return FastMCP("LintGate", description=_MCP_INSTRUCTIONS)  # type: ignore[call-arg]
    except Exception:
        pass
    try:
        return FastMCP("LintGate")
    except Exception:
        return FastMCP()


mcp = _build_mcp_server()


# ─── Layer 2: MCP Resources — lazy-loaded analysis output ────────────────


@mcp.resource("analysis://{tool_name}/{analysis_id}")
def read_analysis_resource(tool_name: str, analysis_id: str) -> str:
    """Read a saved analysis file by tool name and analysis ID."""
    for base in [os.getcwd(), os.environ.get("LINTGATE_PROJECT_ROOT", "")]:
        if not base:
            continue
        filepath = os.path.join(base, ".lintgate", "analysis", tool_name, f"{analysis_id}.json")
        if os.path.isfile(filepath):
            with open(filepath, encoding="utf-8") as f:
                return f.read()
    raise FileNotFoundError(f"Analysis not found: {tool_name}/{analysis_id}")


# ─── Layer 3: Retrieval tool — surgical JSON path queries ────────────────


@mcp.tool()
def query_analysis(
    analysis_id: str,
    tool_name: str = "",
    path: str = "$",
    max_items: int = 20,
) -> str:
    """Query a specific section of a saved analysis by JSON path.

    WHEN TO USE: When you need a specific part of a large analysis result
    without loading the entire file into context.

    Example: query_analysis(analysis_id="cp_abc123", tool_name="controlplane_run", path="blocking_findings")

    Args:
        analysis_id: The analysis ID from a computation tool's response.
        tool_name: The tool that produced the analysis.
        path: Dot-separated JSON path (e.g. "counts.blocking"). Use "$" for root.
        max_items: Max list items to return.
    """
    base = os.getcwd()
    if tool_name:
        search_dirs = [os.path.join(base, ".lintgate", "analysis", tool_name)]
    else:
        analysis_root = os.path.join(base, ".lintgate", "analysis")
        search_dirs = [
            os.path.join(analysis_root, d) for d in os.listdir(analysis_root)
            if os.path.isdir(os.path.join(analysis_root, d))
        ] if os.path.isdir(analysis_root) else []

    filepath = None
    for d in search_dirs:
        candidate = os.path.join(d, f"{analysis_id}.json")
        if os.path.isfile(candidate):
            filepath = candidate
            break
    if not filepath:
        return json.dumps({"error": f"Analysis {analysis_id} not found"})

    with open(filepath, encoding="utf-8") as f:
        data = json.loads(f.read())

    if path and path != "$":
        for key in path.split("."):
            if isinstance(data, dict) and key in data:
                data = data[key]
            elif isinstance(data, list):
                try:
                    data = data[int(key)]
                except (ValueError, IndexError):
                    return json.dumps({"error": f"Path '{path}' not found"})
            else:
                return json.dumps({"error": f"Path '{path}' not found"})

    if isinstance(data, list) and len(data) > max_items:
        total = len(data)
        data = data[:max_items]
        return json.dumps({"result": data, "truncated": total - max_items}, separators=(",", ":"), default=str)
    return json.dumps({"result": data}, separators=(",", ":"), default=str)


# ─── Tier definitions (match tier_selector.py) ──────────────────────────

TIER_LINTERS = {
    0: ["ruff_check"],
    1: [
        "ruff_check",
        "ruff_format",
        "import_checker",
        "version_checker",
        "context_rule_checker",
        "redefinition_checker",
    ],
    2: [
        "ruff_check",
        "ruff_format",
        "mypy",
        "ty",
        "bandit_fast",
        "pip_audit",
        "secret_checker",
        "complexity_checker",
        "structure_checker",
        "version_checker",
        "context_rule_checker",
        "redefinition_checker",
        "performance_checker",
    ],
    3: [
        "ruff_check",
        "ruff_format",
        "mypy",
        "ty",
        "import_checker",
        "complexity_checker",
        "bandit",
        "bandit_fast",
        "pip_audit",
        "secret_checker",
        "structure_checker",
        "architecture_checker",
        "dead_code_checker",
        "version_checker",
        "context_rule_checker",
        "redefinition_checker",
        "performance_checker",
    ],
}

_VALID_STRICTNESS = {"relaxed", "normal", "strict"}


# ─── Helper functions (shared with domain modules via helpers dict) ─────


def _validate_tier(tier: int) -> int:
    if tier not in TIER_LINTERS:
        raise ValueError(f"Invalid tier {tier}; expected one of {sorted(TIER_LINTERS)}")
    return tier


def _validate_strictness(strictness: str) -> str:
    if strictness not in _VALID_STRICTNESS:
        allowed = ", ".join(sorted(_VALID_STRICTNESS))
        raise ValueError(f"Invalid strictness '{strictness}'; expected one of: {allowed}")
    return strictness


def _validate_project_root(path: str, arg_name: str = "path") -> str:
    if not path or not os.path.isdir(path):
        raise ValueError(f"{arg_name} must be an existing directory: {path}")
    return os.path.abspath(path)


def _build_onboarding_status(project_root: str) -> dict[str, Any]:
    """Build machine-readable + human-readable onboarding status.

    Reused by getting_started, controlplane_run, controlplane_status,
    lint_status, and constraint_check to avoid drift across tools.

    Four config states are distinguished:
    - no_config: No .claude/lintgate.yaml found
    - config_no_controlplane_section: Config file exists but lacks controlplane section
    - config_disabled: Config exists but controlplane.enabled is false
    - config_enabled: Fully active
    """
    from lintgate.config import load_controlplane_config

    config_path = os.path.join(project_root, ".claude", "lintgate.yaml")
    config_file_exists = os.path.exists(config_path)
    cp_config = load_controlplane_config(project_root)
    has_controlplane_section = False
    if config_file_exists:
        with contextlib.suppress(Exception):
            import yaml as _yaml

            with open(config_path) as _f:
                _raw = _yaml.safe_load(_f) or {}
            has_controlplane_section = bool(
                isinstance(_raw, dict) and isinstance(_raw.get("controlplane"), dict)
            )

    # Machine-readable flags — always present regardless of state
    status: dict[str, Any] = {
        "config_found": config_file_exists,
        "config_path_checked": config_path,
        "controlplane_enabled": cp_config.enabled if cp_config else False,
        "automatic_hook_active": cp_config.enabled if cp_config else False,
        "using_default_config": cp_config is None,
    }

    # State classification with human-readable hint
    if not config_file_exists:
        status["config_state"] = "no_config"
        status["setup_hint"] = (
            "No config file found. LintGate tools work without config, but to enable "
            "automatic quality checks on every file edit, create .claude/lintgate.yaml with:\n"
            "  controlplane:\n"
            "    enabled: true"
        )
    elif cp_config is None and not has_controlplane_section:
        status["config_state"] = "config_no_controlplane_section"
        status["setup_hint"] = (
            "Config file found, but it has no controlplane section. "
            "Lint tools still work, but automatic quality checks on every file edit "
            "require adding:\n"
            "  controlplane:\n"
            "    enabled: true"
        )
    elif cp_config is not None and not cp_config.enabled:
        status["config_state"] = "config_disabled"
        status["setup_hint"] = (
            "Config file found but ControlPlane is disabled. To enable automatic quality "
            "checks on every file edit, set enabled: true in .claude/lintgate.yaml:\n"
            "  controlplane:\n"
            "    enabled: true"
        )
    else:
        status["config_state"] = "config_enabled"
        # No setup_hint needed — fully configured

    return status


def _collect_python_files(project_root: str) -> list[str]:
    """Recursively find all .py files under project_root."""
    from lintgate.discovery import discover_project_files

    return discover_project_files(project_root)


def _resolve_files(files: list[str], project_root: str) -> tuple[list[str], list[str]]:
    resolved = [
        path if os.path.isabs(path) else os.path.normpath(os.path.join(project_root, path))
        for path in files
    ]
    existing = [path for path in resolved if os.path.exists(path)]
    missing = [path for path in resolved if not os.path.exists(path)]
    return existing, missing


@functools.cache
def _normalize_linter_names(base: tuple[str, ...], extra: tuple[str, ...]) -> list[str]:
    names = [*base, *extra]
    seen: set[str] = set()
    deduped: list[str] = []
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    return deduped


def _build_cp_full_details(mesh_result: Any, finding_index: dict[str, Any]) -> dict[str, Any]:
    """Build full details payload for controlplane_get_details drill-down."""
    details: dict[str, Any] = {
        "coherence": {
            "state": mesh_result.coherence.state,
            "summary": mesh_result.coherence.summary,
            "recommended_action": mesh_result.coherence.recommended_action,
            "silent_channels": list(mesh_result.coherence.silent_channels),
            "loud_channels": list(mesh_result.coherence.loud_channels),
        },
        "duration_ms": mesh_result.duration_ms,
        "partial": mesh_result.partial,
        "incomplete_channels": mesh_result.incomplete_channels,
        "finding_index": finding_index,
        "channels": {},
    }
    for cr in mesh_result.channel_results:
        if cr.status == "skip":
            continue
        channel_data: dict[str, Any] = {
            "status": cr.status,
            "severity": cr.severity,
            "duration_ms": round(cr.duration_ms, 1),
            "error": cr.error_message,
            "findings": [f.to_dict() for f in cr.findings],
            "repairs": [
                {
                    "action_id": r.action_id,
                    "kind": r.kind,
                    "summary": r.summary,
                    "safe": r.safe,
                    "payload": r.payload,
                }
                for r in cr.repairs
            ],
            "metrics": cr.metrics,
        }
        details["channels"][cr.channel] = channel_data
    return details


def _collect_all_issues(aggregated: Any) -> list[LintIssue]:
    return [*aggregated.blocking, *aggregated.warnings, *aggregated.informational]


def _build_linter_diagnostics(results: list[Any]) -> list[dict[str, Any]]:
    diagnostics = []
    for result in sorted(results, key=lambda r: r.linter_name):
        diagnostics.append(
            {
                "linter": result.linter_name,
                "status": result.status,
                "issue_count": len(result.issues),
                "duration_ms": round(result.duration_ms, 1),
                "error": result.error,
            }
        )
    return diagnostics


def _build_next_actions(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate structured next_actions from tool output context.

    Returns a list of serialized NextAction dicts.
    """
    from lintgate.next_action import NextAction, serialize_next_actions

    actions: list[NextAction] = []

    blocking = context.get("blocking", 0)
    fixable = context.get("fixable", 0)
    run_id = context.get("run_id", "")
    warnings = context.get("warnings", 0)

    # If there are fixable issues, suggest lint_fix
    if fixable > 0:
        actions.append(
            NextAction(
                tool="lint_fix",
                args={"path": context.get("project", ""), "dry_run": True},
                reason=f"{fixable} auto-fixable issue{'s' if fixable != 1 else ''}",
                priority=1,
            )
        )

    # If there are blocking issues and a run_id, suggest drill-down
    if blocking > 0 and run_id:
        actions.append(
            NextAction(
                tool="lint_get_details",
                args={"run_id": run_id, "severity": "blocking"},
                reason=f"View {blocking} blocking issue details",
                priority=2,
            )
        )

    # If many warnings, suggest details
    if warnings > 5 and run_id:
        actions.append(
            NextAction(
                tool="lint_get_details",
                args={"run_id": run_id, "severity": "warning"},
                reason=f"View {warnings} warning details",
                priority=3,
            )
        )

    return serialize_next_actions(actions)


_VALID_OUTPUT_MODES = {"compact", "standard", "full"}


def _infer_project_root(payload: Any) -> str | None:
    """Best-effort project root inference from MCP response payload."""
    if not isinstance(payload, dict):
        return None

    for key in ("project_root", "project", "path", "cwd"):
        raw = payload.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        abs_path = os.path.abspath(raw)
        if os.path.isdir(abs_path):
            return abs_path
        if os.path.isfile(abs_path):
            return os.path.dirname(abs_path)
    return None


def _save_analysis(data: Any, tool_name: str, project_root: str, *, run_id: str = "") -> str:
    """Write analysis output to .lintgate/analysis/<tool>/<id>.json. Returns filepath."""
    import hashlib
    analysis_dir = os.path.join(project_root, ".lintgate", "analysis", tool_name)
    os.makedirs(analysis_dir, exist_ok=True)
    serialized = json.dumps(data, separators=(",", ":"), default=str)
    content_hash = hashlib.sha256(serialized.encode()).hexdigest()[:10]
    filename = f"{run_id}.json" if run_id else f"{content_hash}.json"
    filepath = os.path.join(analysis_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(serialized)
    return filepath


def _tool_response(
    data: Any, tool_name: str, project_root: str, summary: str,
    *, run_id: str = "", next_actions: list | None = None, extra: dict[str, Any] | None = None,
) -> str:
    """Save analysis to disk, return slim tool response (~100 tokens)."""
    filepath = _save_analysis(data, tool_name, project_root, run_id=run_id)
    analysis_id = run_id or os.path.basename(filepath).removesuffix(".json")
    response: dict[str, Any] = {"analysis_id": analysis_id, "summary": summary, "file": filepath}
    if extra:
        response.update(extra)
    if next_actions:
        response["next_actions"] = next_actions
    return json.dumps(response, separators=(",", ":"), default=str)


def _json_dumps(data: Any, output_mode: str = "compact") -> str:
    """Serialize data to JSON with mode-appropriate formatting.

    compact/standard: no indent, compact separators (~15-20% smaller)
    full: indent=2 for human readability
    """
    payload = data
    if isinstance(data, dict):
        with contextlib.suppress(Exception):
            from mcp_tools.micro_refresh import attach_session_context

            project_root = _infer_project_root(data)
            if project_root:
                payload = attach_session_context(dict(data), project_root)

    if output_mode == "full":
        return json.dumps(payload, indent=2)
    return json.dumps(payload, separators=(",", ":"))


def _execute_lint_pipeline(
    files: list[str],
    project_root: str,
    tier: int,
    strictness: str,
) -> tuple[Any, list[Any], Any]:
    """Validate inputs, run linters, and aggregate results.

    Returns (aggregated, linter_results, lint_tier).
    """
    _validate_tier(tier)
    _validate_strictness(strictness)

    config = load_config(project_root)
    registry = build_registry(config)

    linter_names = TIER_LINTERS[tier]
    if tier >= 3:
        linter_names = _normalize_linter_names(
            tuple(linter_names), tuple(config.extra_tier3_linters)
        )

    lint_tier = LintTier(
        name=f"tier_{tier}_manual",
        linters=linter_names,
        files=files,
        reason=f"Manual invocation (tier {tier})",
        strictness=strictness,
    )

    linter_results = run_linters(lint_tier, config, registry, timeout_ms=30000)

    aggregated = aggregate_results(
        linter_results,
        config,
        tier_name=lint_tier.name,
        tier_reason=lint_tier.reason,
    )
    return aggregated, linter_results, lint_tier


def _compute_lint_metadata(
    aggregated: Any,
    linter_results: list[Any],
    lint_tier: Any,
    project_root: str,
    files: list[str],
    elapsed_ms: float,
    output_mode: str,
) -> tuple[str, dict[str, Any], dict, Any, dict | None]:
    """Compute recurrence, report, delta, persist state and details.

    Returns (run_id, full_details, recurrence, report, lint_delta).
    """
    all_issues = _collect_all_issues(aggregated)

    recurrence: dict[str, Any] = {
        "repeated_issue_count": 0,
        "unique_signatures_tracked": 0,
        "top_repeated": [],
    }
    with contextlib.suppress(Exception):
        recurrence = update_issue_memory(project_root, all_issues)

    last_run = load_last_run(project_root)
    report = format_report(aggregated, last_run, recurrence_summary=recurrence)

    lint_delta = None
    if last_run is not None:
        previous_index = last_run.get("finding_index")
        if previous_index:
            with contextlib.suppress(Exception):
                from lintgate.lint_delta import compute_lint_delta

                lint_delta = compute_lint_delta(aggregated, previous_index)

    with contextlib.suppress(Exception):
        save_run(project_root, aggregated)

    run_id = generate_run_id()

    full_details: dict[str, Any] = {
        "tier": lint_tier.name,
        "reason": lint_tier.reason,
        "project": project_root,
        "files_linted": len(files),
        "duration_ms": round(elapsed_ms, 1),
        "blocking": len(aggregated.blocking),
        "warnings": len(aggregated.warnings),
        "informational": len(aggregated.informational),
        "total_issues": aggregated.metrics.get("total_issues", 0),
        "fixable": aggregated.metrics.get("fixable_count", 0),
        "linters_run": aggregated.metrics.get("linters_run", 0),
        "linter_statuses": aggregated.linter_statuses,
        "linter_diagnostics": _build_linter_diagnostics(linter_results),
        "recurrence": recurrence,
        "blocking_issues": [issue.to_dict() for issue in aggregated.blocking],
        "warning_issues": [issue.to_dict() for issue in aggregated.warnings],
        "info_issues": [issue.to_dict() for issue in aggregated.informational],
    }
    if report:
        full_details["report"] = report.get("systemMessage", "")

    with contextlib.suppress(Exception):
        save_run_details(run_id, full_details)

    with contextlib.suppress(Exception):
        log_metric(
            {
                "event": "mcp_lint_run",
                "project": project_root,
                "tier": lint_tier.name,
                "files_count": len(files),
                "blocking_count": len(aggregated.blocking),
                "warning_count": len(aggregated.warnings),
                "info_count": len(aggregated.informational),
                "linters_run": aggregated.metrics.get("linters_run", 0),
                "duration_ms": round(elapsed_ms, 1),
                "repeated_issue_count": recurrence.get("repeated_issue_count", 0),
                "output_mode": output_mode,
            }
        )

    return run_id, full_details, recurrence, report, lint_delta


def _build_compact_output(
    run_id: str,
    lint_tier: Any,
    files: list[str],
    elapsed_ms: float,
    aggregated: Any,
    lint_delta: dict | None,
    max_findings: int,
) -> dict[str, Any]:
    """Build compact output: counts + optional blocking issues + delta."""
    output: dict[str, Any] = {
        "run_id": run_id,
        "tier": lint_tier.name,
        "files_linted": len(files),
        "duration_ms": round(elapsed_ms, 1),
        "blocking": len(aggregated.blocking),
        "warnings": len(aggregated.warnings),
        "informational": len(aggregated.informational),
        "fixable": aggregated.metrics.get("fixable_count", 0),
    }
    if aggregated.blocking:
        output["blocking_issues"] = [
            {
                "id": i.issue_id,
                "kind": i.kind,
                "loc": i.short_location(),
                "msg": i.message[:80],
            }
            for i in aggregated.blocking[:max_findings]
        ]
        if len(aggregated.blocking) > max_findings:
            output["blocking_truncated"] = len(aggregated.blocking) - max_findings
    if lint_delta is not None:
        output["delta"] = {
            "resolved": lint_delta["resolved_count"],
            "new": len(lint_delta["new"]),
            "remaining": lint_delta["still_active_count"],
            "summary": lint_delta.get("summary", ""),
        }
    return output


def _build_standard_output(
    run_id: str,
    lint_tier: Any,
    files: list[str],
    elapsed_ms: float,
    aggregated: Any,
    lint_delta: dict | None,
    max_findings: int,
) -> dict[str, Any]:
    """Build standard output: counts + issue details + delta."""
    output: dict[str, Any] = {
        "run_id": run_id,
        "tier": lint_tier.name,
        "files_linted": len(files),
        "duration_ms": round(elapsed_ms, 1),
        "blocking": len(aggregated.blocking),
        "warnings": len(aggregated.warnings),
        "informational": len(aggregated.informational),
        "fixable": aggregated.metrics.get("fixable_count", 0),
        "linters_run": aggregated.metrics.get("linters_run", 0),
        "linter_statuses": aggregated.linter_statuses,
    }
    if aggregated.blocking:
        output["blocking_issues"] = [
            issue.to_dict() for issue in aggregated.blocking[:max_findings]
        ]
        if len(aggregated.blocking) > max_findings:
            output["blocking_truncated"] = len(aggregated.blocking) - max_findings
    if aggregated.warnings:
        remaining = max(0, max_findings - len(aggregated.blocking))
        if remaining > 0:
            output["warning_issues"] = [
                issue.to_dict() for issue in aggregated.warnings[:remaining]
            ]
            if len(aggregated.warnings) > remaining:
                output["warnings_truncated"] = len(aggregated.warnings) - remaining
    if lint_delta is not None:
        output["delta"] = lint_delta
    return output


def _build_lint_response(
    output_mode: str,
    run_id: str,
    lint_tier: Any,
    files: list[str],
    elapsed_ms: float,
    aggregated: Any,
    full_details: dict[str, Any],
    lint_delta: dict | None,
    project_root: str,
    max_findings: int,
) -> dict[str, Any]:
    """Build the response dict based on output_mode (compact/standard/full)."""
    output: dict[str, Any]
    if output_mode == "compact":
        output = _build_compact_output(
            run_id,
            lint_tier,
            files,
            elapsed_ms,
            aggregated,
            lint_delta,
            max_findings,
        )
    elif output_mode == "standard":
        output = _build_standard_output(
            run_id,
            lint_tier,
            files,
            elapsed_ms,
            aggregated,
            lint_delta,
            max_findings,
        )
    else:
        output = {"run_id": run_id, **full_details}
        if lint_delta is not None:
            output["delta"] = lint_delta

    output["next_actions"] = _build_next_actions({**output, "project": project_root})
    return output


def _run_lint(
    files: list[str],
    project_root: str,
    tier: int,
    strictness: str = "normal",
    output_mode: str = "compact",
    max_findings: int = 20,
) -> dict[str, Any]:
    """Core lint execution shared by lint_files and lint_project.

    Output modes:
    - compact: ~200 tokens — counts, run_id, duration, next_actions only
    - standard: compact + blocking_issues + warning_issues (capped by max_findings)
    - full: everything including informational, recurrence, diagnostics, report
    """
    start = time.perf_counter()
    if output_mode not in _VALID_OUTPUT_MODES:
        output_mode = "compact"

    aggregated, linter_results, lint_tier = _execute_lint_pipeline(
        files,
        project_root,
        tier,
        strictness,
    )

    elapsed_ms = (time.perf_counter() - start) * 1000

    run_id, full_details, recurrence, report, lint_delta = _compute_lint_metadata(
        aggregated,
        linter_results,
        lint_tier,
        project_root,
        files,
        elapsed_ms,
        output_mode,
    )

    return _build_lint_response(
        output_mode,
        run_id,
        lint_tier,
        files,
        elapsed_ms,
        aggregated,
        full_details,
        lint_delta,
        project_root,
        max_findings,
    )


# ─── Build helpers dict and register domain tools ────────────────────────

_helpers = {
    "_validate_project_root": _validate_project_root,
    "_json_dumps": _json_dumps,
    # NOTE: _save_analysis and _tool_response live in mcp_tools/_disk_helpers.py
    # Tool files import them directly to avoid stale-dict issues with long-lived
    # MCP server processes. Do NOT add new shared utilities here — put them in
    # standalone modules under mcp_tools/ instead.
    "_build_onboarding_status": _build_onboarding_status,
    "_collect_python_files": _collect_python_files,
    "_resolve_files": _resolve_files,
    "_run_lint": _run_lint,
    "_build_cp_full_details": _build_cp_full_details,
    "_normalize_linter_names": _normalize_linter_names,
    "_build_next_actions": _build_next_actions,
    "_collect_all_issues": _collect_all_issues,
    "_build_linter_diagnostics": _build_linter_diagnostics,
    "TIER_LINTERS": TIER_LINTERS,
    "_VALID_STRICTNESS": _VALID_STRICTNESS,
}

from mcp_tools import register_all  # noqa: E402

# Register all domain tools and expose them as module-level attributes
# so existing code (tests, etc.) can do `from mcp_server import behavior_precheck`.
_tool_funcs = register_all(mcp, _helpers)

# Explicitly export tools to satisfy static analyzers and typing contracts.
from mcp_tools.registry_types import ToolRegistry  # noqa: E402

analyze_test_strength = _tool_funcs["analyze_test_strength"]
audit_context_health = _tool_funcs["audit_context_health"]
audit_tool_versions = _tool_funcs["audit_tool_versions"]
behavior_precheck = _tool_funcs["behavior_precheck"]
bootstrap_context_files = _tool_funcs["bootstrap_context_files"]
build_theory_pack = _tool_funcs["build_theory_pack"]
compass_check = _tool_funcs["compass_check"]
compass_interview = _tool_funcs["compass_interview"]
compass_reset = _tool_funcs["compass_reset"]
compass_status = _tool_funcs["compass_status"]
compass_update = _tool_funcs["compass_update"]
constraint_check = _tool_funcs["constraint_check"]
context_guidance = _tool_funcs["context_guidance"]
context_patch_apply = _tool_funcs["context_patch_apply"]
context_patch_review = _tool_funcs["context_patch_review"]
controlplane_agent_feedback = _tool_funcs["controlplane_agent_feedback"]
controlplane_apply_repairs = _tool_funcs["controlplane_apply_repairs"]
controlplane_get_details = _tool_funcs["controlplane_get_details"]
controlplane_report_repair = _tool_funcs["controlplane_report_repair"]
controlplane_run = _tool_funcs["controlplane_run"]
controlplane_status = _tool_funcs["controlplane_status"]
controlplane_test_skeleton = _tool_funcs["controlplane_test_skeleton"]
declare_mode = _tool_funcs["declare_mode"]
dep_health_check = _tool_funcs["dep_health_check"]
dep_sync = _tool_funcs["dep_sync"]
extract_project_theory = _tool_funcs["extract_project_theory"]
extract_theory_constraints = _tool_funcs["extract_theory_constraints"]
generate_property_tests = _tool_funcs["generate_property_tests"]
get_theory_context = _tool_funcs["get_theory_context"]
getting_started = _tool_funcs["getting_started"]
global_memory_reset = _tool_funcs["global_memory_reset"]
global_memory_status = _tool_funcs["global_memory_status"]
habit_compact = _tool_funcs["habit_compact"]
habit_configure = _tool_funcs["habit_configure"]
habit_status = _tool_funcs["habit_status"]
hygiene_check = _tool_funcs["hygiene_check"]
inspect_algebra = _tool_funcs["inspect_algebra"]
inspect_test_assertions = _tool_funcs["inspect_test_assertions"]
lint_files = _tool_funcs["lint_files"]
lint_fix = _tool_funcs["lint_fix"]
lint_get_details = _tool_funcs["lint_get_details"]
lint_project = _tool_funcs["lint_project"]
lint_status = _tool_funcs["lint_status"]
model_profile_probe_start = _tool_funcs["model_profile_probe_start"]
model_profile_probe_submit = _tool_funcs["model_profile_probe_submit"]
model_profile_status = _tool_funcs["model_profile_status"]
offline_analysis_generate = _tool_funcs["offline_analysis_generate"]
offline_analysis_run = _tool_funcs["offline_analysis_run"]
prediction_register = _tool_funcs["prediction_register"]
prescriptive_spec_compile = _tool_funcs["prescriptive_spec_compile"]
prescriptive_spec_compose = _tool_funcs["prescriptive_spec_compose"]
prescriptive_spec_status = _tool_funcs["prescriptive_spec_status"]
prescriptive_spec_verify = _tool_funcs["prescriptive_spec_verify"]
refactor_move = _tool_funcs["refactor_move"]
scaffold_config = _tool_funcs["scaffold_config"]
setup_github_quality = _tool_funcs["setup_github_quality"]
setup_hooks = _tool_funcs["setup_hooks"]
telemetry_summary = _tool_funcs["telemetry_summary"]
theory_mode_enter = _tool_funcs["theory_mode_enter"]
theory_mode_freeze = _tool_funcs["theory_mode_freeze"]
convergence_analyze = _tool_funcs["convergence_analyze"]
extraction_plan = _tool_funcs["extraction_plan"]
optimization_landscape = _tool_funcs["optimization_landscape"]
test_hygiene_scan = _tool_funcs["test_hygiene_scan"]
test_triage = _tool_funcs["test_triage"]
test_infer_inputs = _tool_funcs["test_infer_inputs"]
test_characterize = _tool_funcs["test_characterize"]
test_characterize_mark = _tool_funcs["test_characterize_mark"]
test_redundancy_project = _tool_funcs["test_redundancy_project"]

__all__ = [
    "mcp",
    "ToolRegistry",
    "analyze_test_strength",
    "audit_context_health",
    "audit_tool_versions",
    "behavior_precheck",
    "bootstrap_context_files",
    "build_theory_pack",
    "compass_check",
    "compass_interview",
    "compass_reset",
    "compass_status",
    "compass_update",
    "constraint_check",
    "context_guidance",
    "context_patch_apply",
    "context_patch_review",
    "controlplane_agent_feedback",
    "controlplane_apply_repairs",
    "controlplane_get_details",
    "controlplane_report_repair",
    "controlplane_run",
    "controlplane_status",
    "controlplane_test_skeleton",
    "declare_mode",
    "dep_health_check",
    "dep_sync",
    "extract_project_theory",
    "extract_theory_constraints",
    "generate_property_tests",
    "get_theory_context",
    "getting_started",
    "global_memory_reset",
    "global_memory_status",
    "habit_compact",
    "habit_configure",
    "habit_status",
    "hygiene_check",
    "inspect_algebra",
    "inspect_test_assertions",
    "lint_files",
    "lint_fix",
    "lint_get_details",
    "lint_project",
    "lint_status",
    "model_profile_probe_start",
    "model_profile_probe_submit",
    "model_profile_status",
    "offline_analysis_generate",
    "offline_analysis_run",
    "prediction_register",
    "prescriptive_spec_compile",
    "prescriptive_spec_compose",
    "prescriptive_spec_status",
    "prescriptive_spec_verify",
    "refactor_move",
    "scaffold_config",
    "setup_github_quality",
    "setup_hooks",
    "telemetry_summary",
    "theory_mode_enter",
    "theory_mode_freeze",
    "convergence_analyze",
    "extraction_plan",
    "optimization_landscape",
    "test_hygiene_scan",
    "test_triage",
    "test_infer_inputs",
    "test_characterize",
    "test_characterize_mark",
    "test_redundancy_project",
]

# ─── Version constant (referenced by test_mcp_schema_contracts) ─────────
# lint_status reports "version": "0.2.0" — keep this here for source-level checks.
_VERSION = "0.2.0"

# ─── Entry point ────────────────────────────────────────────────────────


def run_server() -> None:
    from lintgate.mcp_schema import (
        ProviderSchemaError,
        compile_and_validate_schemas,
        enforce_mcp_contract,
    )

    try:
        tools = mcp._tool_manager.list_tools()
        compile_and_validate_schemas(tools, agent_profile="strict")
        enforce_mcp_contract(tools)
    except ProviderSchemaError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(1)

    mcp.run()


if __name__ == "__main__":
    run_server()
