#!/usr/bin/env python3
"""LintGate MCP Server — manual lint invocation for Claude Code.

Exposes LintGate as structured MCP tools so the agent (or user) can
trigger linting explicitly, not just through the PostToolUse hook.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Error: mcp package not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

try:
    from lintgate.agent_reporter import format_report
    from lintgate.config import load_config
    from lintgate.context_bootstrap import bootstrap_context_files as _bootstrap_context_files
    from lintgate.context_guidance import build_context_guidance, summarize_context_guidance
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import (
        METRICS_DIR,
        generate_run_id,
        load_last_run,
        load_last_version_audit,
        load_run_details,
        log_metric,
        log_version_event,
        save_run,
        save_run_details,
        save_version_audit,
        update_issue_memory,
    )
    from lintgate.types import LintIssue, LintTier
    from lintgate.versioning import format_version_audit_summary, run_version_audit
except ModuleNotFoundError:
    _LINTGATE_DIR = Path(__file__).resolve().parent
    if str(_LINTGATE_DIR) not in sys.path:
        sys.path.insert(0, str(_LINTGATE_DIR))
    from lintgate.agent_reporter import format_report
    from lintgate.config import load_config
    from lintgate.context_bootstrap import bootstrap_context_files as _bootstrap_context_files
    from lintgate.context_guidance import build_context_guidance, summarize_context_guidance
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import (
        METRICS_DIR,
        generate_run_id,
        load_last_run,
        load_last_version_audit,
        load_run_details,
        log_metric,
        log_version_event,
        save_run,
        save_run_details,
        save_version_audit,
        update_issue_memory,
    )
    from lintgate.types import LintIssue, LintTier
    from lintgate.versioning import format_version_audit_summary, run_version_audit

_MCP_INSTRUCTIONS = (
    "LintGate: code quality analysis for Python projects. "
    "Start with getting_started(path) for project-specific guidance.\n"
    "Essential workflow (6 core tools):\n"
    "  1. lint_files — lint files you just edited\n"
    "  2. lint_project — full project lint scan\n"
    "  3. lint_fix — auto-fix safe issues\n"
    "  4. controlplane_run — comprehensive project health check "
    "(lint + tests + deps + git; works without config)\n"
    "  5. controlplane_get_details — drill into health check findings\n"
    "  6. bootstrap_context_files — generate project-specific CLAUDE.md\n"
    "First session: controlplane_run(path) → controlplane_get_details(run_id) → "
    "lint_fix → bootstrap_context_files(path, write=true).\n"
    "All responses include next_actions with suggested follow-up tools. "
    "32 tools total — use getting_started or lint_status to explore."
)


def _build_mcp_server() -> FastMCP:
    """Instantiate FastMCP across mcp package versions."""
    try:
        sig = inspect.signature(FastMCP.__init__)
        if "instructions" in sig.parameters:
            return FastMCP("LintGate", instructions=_MCP_INSTRUCTIONS)
        if "description" in sig.parameters:
            return FastMCP("LintGate", description=_MCP_INSTRUCTIONS)
    except Exception:
        pass
    try:
        return FastMCP("LintGate")
    except Exception:
        return FastMCP()


mcp = _build_mcp_server()


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
        "complexity_checker",
        "structure_checker",
        "version_checker",
        "context_rule_checker",
        "redefinition_checker",
    ],
    3: [
        "ruff_check",
        "ruff_format",
        "mypy",
        "import_checker",
        "complexity_checker",
        "bandit",
        "structure_checker",
        "architecture_checker",
        "dead_code_checker",
        "version_checker",
        "context_rule_checker",
        "redefinition_checker",
    ],
}

_VALID_STRICTNESS = {"relaxed", "normal", "strict"}
_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".eggs",
}


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
    lint_status, and behavior_precheck to avoid drift across tools.

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
    elif not cp_config.enabled:
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
    py_files = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        # Prune uninteresting directories.
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                py_files.append(os.path.join(dirpath, name))

    return sorted(py_files)


def _resolve_files(files: list[str], project_root: str) -> tuple[list[str], list[str]]:
    resolved = [
        path if os.path.isabs(path) else os.path.normpath(os.path.join(project_root, path))
        for path in files
    ]
    existing = [path for path in resolved if os.path.exists(path)]
    missing = [path for path in resolved if not os.path.exists(path)]
    return existing, missing


def _normalize_linter_names(base: list[str], extra: list[str]) -> list[str]:
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

    Returns a list of suggested follow-up tool calls with priority.
    """
    actions: list[dict[str, Any]] = []

    blocking = context.get("blocking", 0)
    fixable = context.get("fixable", 0)
    run_id = context.get("run_id", "")
    warnings = context.get("warnings", 0)

    # If there are fixable issues, suggest lint_fix
    if fixable > 0:
        actions.append(
            {
                "tool": "lint_fix",
                "args": {"path": context.get("project", ""), "dry_run": True},
                "safe": True,
                "reason": f"{fixable} auto-fixable issue{'s' if fixable != 1 else ''}",
                "priority": 1,
            }
        )

    # If there are blocking issues and a run_id, suggest drill-down
    if blocking > 0 and run_id:
        actions.append(
            {
                "tool": "lint_get_details",
                "args": {"run_id": run_id, "severity": "blocking"},
                "safe": True,
                "reason": f"View {blocking} blocking issue details",
                "priority": 2,
            }
        )

    # If many warnings, suggest details
    if warnings > 5 and run_id:
        actions.append(
            {
                "tool": "lint_get_details",
                "args": {"run_id": run_id, "severity": "warning"},
                "safe": True,
                "reason": f"View {warnings} warning details",
                "priority": 3,
            }
        )

    return actions


_VALID_OUTPUT_MODES = {"compact", "standard", "full"}


def _json_dumps(data: Any, output_mode: str = "compact") -> str:
    """Serialize data to JSON with mode-appropriate formatting.

    compact/standard: no indent, compact separators (~15-20% smaller)
    full: indent=2 for human readability
    """
    if output_mode == "full":
        return json.dumps(data, indent=2)
    return json.dumps(data, separators=(",", ":"))


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
    _validate_tier(tier)
    _validate_strictness(strictness)
    if output_mode not in _VALID_OUTPUT_MODES:
        output_mode = "compact"

    config = load_config(project_root)
    registry = build_registry(config)

    linter_names = TIER_LINTERS[tier]
    if tier >= 3:
        linter_names = _normalize_linter_names(linter_names, config.extra_tier3_linters)

    lint_tier = LintTier(
        name=f"tier_{tier}_manual",
        linters=linter_names,
        files=files,
        reason=f"Manual invocation (tier {tier})",
        strictness=strictness,
    )

    # Run linters.
    linter_results = run_linters(lint_tier, config, registry, timeout_ms=30000)

    # Aggregate.
    aggregated = aggregate_results(
        linter_results,
        config,
        tier_name=lint_tier.name,
        tier_reason=lint_tier.reason,
    )
    all_issues = _collect_all_issues(aggregated)

    recurrence = {"repeated_issue_count": 0, "unique_signatures_tracked": 0, "top_repeated": []}
    with contextlib.suppress(Exception):
        recurrence = update_issue_memory(project_root, all_issues)

    # Format report.
    last_run = load_last_run(project_root)
    report = format_report(aggregated, last_run, recurrence_summary=recurrence)

    # Save state.
    with contextlib.suppress(Exception):
        save_run(project_root, aggregated)

    # Generate run_id and persist full details for drill-down.
    run_id = generate_run_id()
    elapsed_ms = (time.perf_counter() - start) * 1000

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

    # Log metric.
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

    # ── Build response based on output_mode ──

    if output_mode == "compact":
        # ~200 tokens: counts + run_id + duration + next_actions
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
        # Include blocking issue summaries even in compact (they're critical)
        if aggregated.blocking:
            output["blocking_issues"] = [
                {"id": i.issue_id, "kind": i.kind, "loc": i.short_location(), "msg": i.message[:80]}
                for i in aggregated.blocking[:max_findings]
            ]
            if len(aggregated.blocking) > max_findings:
                output["blocking_truncated"] = len(aggregated.blocking) - max_findings
        output["next_actions"] = _build_next_actions({**output, "project": project_root})
        return output

    elif output_mode == "standard":
        # ~500 tokens: compact + full blocking + warnings (capped)
        output = {
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
        output["next_actions"] = _build_next_actions({**output, "project": project_root})
        return output

    else:
        # full: everything (backward-compatible)
        output = {
            "run_id": run_id,
            **full_details,
        }
        output["next_actions"] = _build_next_actions({**output, "project": project_root})
        return output


# ─── MCP Tools ──────────────────────────────────────────────────────────


@mcp.tool()
def getting_started(path: str) -> str:
    """Start here. Get oriented with LintGate on any project.

    WHEN TO USE: First time using LintGate on a project, or when unsure
    what to do next. Returns project status, recommended next steps, and
    the essential tool workflow.

    Example: getting_started(path="/my/project")
    """
    project_root = _validate_project_root(path)

    config_status = _build_onboarding_status(project_root)

    # Build dynamic next_actions based on project state
    next_actions: list[dict[str, str]] = []
    if config_status["config_state"] != "config_enabled":
        next_actions.append({
            "tool": "controlplane_run",
            "reason": "Run a comprehensive health check (works without config)",
            "example": f'controlplane_run(path="{project_root}")',
        })
    else:
        next_actions.append({
            "tool": "controlplane_run",
            "reason": "Run a comprehensive health check",
            "example": f'controlplane_run(path="{project_root}")',
        })

    # Check if bootstrap files exist
    claude_md = os.path.join(project_root, ".claude", "CLAUDE.md")
    if not os.path.exists(claude_md):
        next_actions.append({
            "tool": "bootstrap_context_files",
            "reason": "Generate project-specific CLAUDE.md with documented principles",
            "example": f'bootstrap_context_files(path="{project_root}", write=True)',
        })

    next_actions.append({
        "tool": "lint_project",
        "reason": "Full project lint scan",
        "example": f'lint_project(path="{project_root}")',
    })

    output: dict[str, Any] = {
        "project": project_root,
        "config_status": config_status,
        "essential_tools": {
            "lint_files": "Check specific files after edits — "
            'lint_files(files=["/path/to/file.py"])',
            "lint_project": "Full project scan — "
            'lint_project(path="/my/project")',
            "lint_fix": "Auto-fix safe issues — "
            'lint_fix(path="/my/project", dry_run=False)',
            "controlplane_run": "Comprehensive health check (lint + tests + deps + git) — "
            'controlplane_run(path="/my/project")',
            "controlplane_get_details": "Drill into health check findings — "
            'controlplane_get_details(run_id="...")',
            "bootstrap_context_files": "Generate project CLAUDE.md — "
            'bootstrap_context_files(path="/my/project", write=True)',
        },
        "first_session_workflow": [
            "1. Run controlplane_run(path) for a full project health check",
            "2. Run controlplane_get_details(run_id) to review specific findings",
            "3. Run lint_fix(path, dry_run=False) to auto-fix safe issues",
            "4. Run bootstrap_context_files(path, write=true) to generate persistent context files",
        ],
        "all_tools_count": 32,
        "next_actions": next_actions,
    }

    return json.dumps(output, indent=2)


@mcp.tool()
def lint_files(
    files: list[str],
    tier: Literal[0, 1, 2, 3] = 2,
    project_root: str | None = None,
    strictness: Literal["relaxed", "normal", "strict"] = "normal",
) -> str:
    """Lint specific files at a given tier level.

    WHEN TO USE: After editing Python files. This is the most common tool —
    call it after every code change to catch issues early.

    Example: lint_files(files=["/my/project/src/main.py"])

    Returns compact JSON with run_id, issue counts, and next_actions.
    Use lint_get_details(run_id) to drill into full issue details.
    Use lint_fix() to auto-fix safe issues found.
    """
    if not files:
        raise ValueError("No files specified")

    resolved_project_root = (
        _validate_project_root(project_root, arg_name="project_root")
        if project_root
        else os.path.dirname(os.path.abspath(files[0]))
    )
    existing, missing = _resolve_files(files, resolved_project_root)

    if not existing:
        raise ValueError(f"No specified files exist. Missing: {missing}")

    result = _run_lint(
        existing,
        resolved_project_root,
        int(tier),
        strictness,
        output_mode="compact",
    )
    if missing:
        result["missing_files"] = missing
    return _json_dumps(result, "compact")


@mcp.tool()
def lint_project(
    path: str,
    tier: Literal[0, 1, 2, 3] = 2,
    strictness: Literal["relaxed", "normal", "strict"] = "normal",
) -> str:
    """Lint all Python files in a project at a given tier level.

    WHEN TO USE: For a full project scan — run at the start of a session
    or before committing. For checking specific files after edits, use lint_files instead.

    Example: lint_project(path="/my/project")

    Returns compact JSON with run_id, issue counts, and next_actions.
    Use lint_get_details(run_id) to drill into full issue details.
    Use lint_fix(path) to auto-fix safe issues found.
    """
    project_root = _validate_project_root(path)

    py_files = _collect_python_files(project_root)
    if not py_files:
        raise ValueError(f"No Python files found under: {project_root}")

    result = _run_lint(
        py_files,
        project_root,
        int(tier),
        strictness,
        output_mode="compact",
    )
    result["total_python_files"] = len(py_files)
    return _json_dumps(result, "compact")


@mcp.tool()
def lint_get_details(
    run_id: str,
    severity: str | None = None,
    max_issues: int = 10,
    include_recurrence: bool = False,
) -> str:
    """Drill into a previous lint run by run_id.

    Use after lint_files/lint_project (which return a run_id in compact mode)
    to retrieve full issue details without re-running linters.

    Args:
        run_id: The run_id from a previous lint_files/lint_project response.
        severity: Filter by severity: "blocking", "warning", "informational", or None for all.
        max_issues: Maximum issues to return (default 10).
        include_recurrence: Include recurrence data from issue memory.
    """
    details = load_run_details(run_id)
    if details is None:
        raise ValueError(f"No lint run found with run_id: {run_id}")

    valid_severities = {"blocking", "warning", "informational", None}
    if severity not in valid_severities:
        raise ValueError(
            f"Invalid severity '{severity}'; expected one of: blocking, warning, informational"
        )

    output: dict[str, Any] = {
        "run_id": run_id,
        "tier": details.get("tier", ""),
        "project": details.get("project", ""),
        "duration_ms": details.get("duration_ms", 0),
    }

    # Collect requested issues
    issues: list[dict[str, Any]] = []
    if severity is None or severity == "blocking":
        issues.extend(details.get("blocking_issues", []))
    if severity is None or severity == "warning":
        issues.extend(details.get("warning_issues", []))
    if severity is None or severity == "informational":
        issues.extend(details.get("info_issues", []))

    output["total_matching"] = len(issues)
    output["issues"] = issues[:max_issues]
    if len(issues) > max_issues:
        output["truncated"] = len(issues) - max_issues

    if include_recurrence:
        output["recurrence"] = details.get("recurrence", {})

    # Also include linter diagnostics for context
    if details.get("linter_diagnostics"):
        output["linter_diagnostics"] = details["linter_diagnostics"]

    return _json_dumps(output, "standard")


@mcp.tool()
def lint_status(path: str | None = None) -> str:
    """Show LintGate status: linters, run history, context, version audits, and today's metrics."""
    project_root = _validate_project_root(path) if path else os.getcwd()

    status: dict[str, Any] = {
        "version": "0.2.0",
    }

    config = load_config(project_root)
    registry = build_registry(config)
    linters_info = {}
    for name, linter in sorted(registry.items()):
        linters_info[name] = {
            "tier": linter.tier,
            "available": linter.available(),
            "tool": linter.required_tool,
        }
    status["linters"] = linters_info
    status["linter_count"] = len(linters_info)

    status["project"] = project_root
    status["config"] = {
        "languages": config.languages,
        "pipeline_critical_paths": config.pipeline_critical_paths,
        "severity_overrides": config.severity_overrides,
        "enabled_linters": config.enabled_linters,
        "tool_version_requirements": config.tool_version_requirements,
    }

    last_run = load_last_run(project_root)
    if last_run:
        from datetime import datetime as dt

        ts = last_run.get("timestamp", 0)
        last_run["timestamp_human"] = dt.fromtimestamp(ts).isoformat()
        status["last_run"] = last_run
    else:
        status["last_run"] = None

    version_audit = load_last_version_audit(project_root)
    if version_audit:
        status["last_version_audit"] = {
            "summary": format_version_audit_summary(version_audit),
            "issues": version_audit.get("issues", []),
        }
    else:
        status["last_version_audit"] = None

    guidance = build_context_guidance(project_root)
    status["context_guidance"] = summarize_context_guidance(guidance)

    # Recent metrics summary.
    try:
        from datetime import datetime as dt

        today = dt.now().strftime("%Y%m%d")
        metrics_file = METRICS_DIR / f"lintgate_{today}.jsonl"
        if metrics_file.exists():
            with open(metrics_file) as f:
                lines = f.readlines()
            total_runs = len(lines)
            total_blocking = 0
            total_duration = 0.0
            tiers_used: dict[str, int] = {}

            for line in lines:
                try:
                    entry = json.loads(line)
                    total_blocking += entry.get("blocking_count", 0)
                    total_duration += entry.get("duration_ms", 0)
                    tier = entry.get("tier", "unknown")
                    tiers_used[tier] = tiers_used.get(tier, 0) + 1
                except json.JSONDecodeError:
                    continue

            status["today_metrics"] = {
                "total_runs": total_runs,
                "total_blocking_found": total_blocking,
                "avg_duration_ms": round(total_duration / max(total_runs, 1), 1),
                "tier_distribution": tiers_used,
            }
    except Exception:
        status["today_metrics"] = None

    # Surface onboarding when ControlPlane is not fully configured
    _onboarding = _build_onboarding_status(project_root)
    if _onboarding.get("config_state") != "config_enabled":
        status["onboarding"] = _onboarding

    return json.dumps(status, indent=2)


@mcp.tool()
def audit_tool_versions(
    path: str,
    auto_fix: bool = False,
    verify_after_fix: bool = True,
) -> str:
    """Audit lint tool version compatibility and optionally repair mismatches.

    Compares installed tool versions against requirements in lintgate.yaml.
    Set auto_fix=True to attempt automatic upgrades via pip/uv.
    """
    project_root = _validate_project_root(path)
    config = load_config(project_root)

    audit = run_version_audit(
        project_root,
        config_requirements=config.tool_version_requirements,
        auto_fix=auto_fix,
        verify_after_fix=verify_after_fix,
    )

    summary = format_version_audit_summary(audit)

    with contextlib.suppress(Exception):
        save_version_audit(project_root, audit)

    with contextlib.suppress(Exception):
        log_version_event(
            {
                "event": "audit_tool_versions",
                "project": project_root,
                "auto_fix": auto_fix,
                "issue_count": summary.get("issue_count", 0),
                "post_fix_issue_count": summary.get("post_fix_issue_count"),
            }
        )

    return json.dumps(
        {
            "summary": summary,
            **audit,
        },
        indent=2,
    )


@mcp.tool()
def context_guidance(
    path: str,
    files: list[str] | None = None,
) -> str:
    """Summarize context guidance and machine-usable rules for a project."""
    project_root = _validate_project_root(path)
    guidance = build_context_guidance(project_root, files=files)
    guidance["summary"] = summarize_context_guidance(guidance)
    return json.dumps(guidance, indent=2)


@mcp.tool()
def audit_context_health(path: str) -> str:
    """Audit CLAUDE.md/AGENTS.md quality against LLM context file best practices.

    Checks: length (configurable), structure, staleness, contradictions,
    machine-rule coverage, and path reference validation.

    Configure thresholds in lintgate.yaml under linters.context_auditor.
    """
    from lintgate.context_auditor import audit_context_health as _audit

    return json.dumps(_audit(_validate_project_root(path)), indent=2)


@mcp.tool()
def bootstrap_context_files(
    path: str,
    write: bool = False,
    overwrite: bool = False,
    include_theory_rules_doc: bool = True,
    max_machine_rules: int = 12,
    model_id: str | None = None,
    use_model_profile: bool = True,
) -> str:
    """Generate project-specific CLAUDE.md and AGENTS.md from documented principles.

    WHEN TO USE: On first session with a project, or when project documentation
    changes significantly. Scans all markdown docs in the repo to extract principles,
    anti-patterns, and lint rules, then generates context files that persist across sessions.

    Example: bootstrap_context_files(path="/my/project", write=True)

    Generates:
    - CLAUDE.md — project principles, anti-patterns, and enforceable lint rules
    - AGENTS.md — tool reference for all agents
    - .claude/rules/theory.md (optional) — extracted principles as rules

    Default mode is non-destructive (`write=false`) — returns drafts for review.
    Set `write=true` to create/update files on disk.

    Returns `needs_review` — items where automated analysis was uncertain and the
    agent can cheaply resolve. Returns `quick_wins` — concrete next steps.
    Returns `agent_instructions` — ordered workflow for what to do with the result.
    """
    project_root = _validate_project_root(path)
    return json.dumps(
        _bootstrap_context_files(
            project_root,
            write=write,
            overwrite=overwrite,
            include_theory_rules_doc=include_theory_rules_doc,
            max_machine_rules=max_machine_rules,
            model_id=model_id,
            use_model_profile=use_model_profile,
        ),
        indent=2,
    )


@mcp.tool()
def context_patch_review(path: str) -> str:
    """Review pending updates to CLAUDE.md auto-managed sections.

    Shows pending patches with diff previews. Use context_patch_apply
    to write the changes after reviewing.

    Args:
        path: Project root path.
    """
    from lintgate.context_bootstrap import ContextPatch, apply_context_patch, generate_context_patch
    from lintgate.controlplane.session_memory import get_or_create_session

    project_root = _validate_project_root(path)
    session = get_or_create_session(project_root)

    pending = [p for p in session.pending_patches if p.get("status", "pending") == "pending"]

    if not pending:
        return json.dumps({"pending_count": 0, "message": "No pending context patches."}, indent=2)

    previews = []
    for p_dict in pending:
        patch = ContextPatch.from_dict(p_dict)
        # Rebuild patch from current file state so preview reflects cumulative changes.
        refreshed = generate_context_patch(
            project_root,
            trigger=patch.trigger,
            evidence=patch.evidence,
        )
        if refreshed is None:
            previews.append(
                {
                    "patch_id": patch.patch_id,
                    "section_id": patch.section_id,
                    "trigger": patch.trigger,
                    "rationale": patch.rationale,
                    "diff_preview": None,
                    "status": "no_op",
                }
            )
            continue

        # Preserve original patch id for stable review/apply UX.
        refreshed.patch_id = patch.patch_id
        preview = apply_context_patch(project_root, refreshed, dry_run=True)
        previews.append(
            {
                "patch_id": refreshed.patch_id,
                "section_id": refreshed.section_id,
                "trigger": refreshed.trigger,
                "rationale": refreshed.rationale,
                "diff_preview": preview.get("diff_preview"),
                "status": "pending",
            }
        )

    return json.dumps(
        {
            "pending_count": len(pending),
            "patches": previews,
            "next_actions": [
                {
                    "tool": "context_patch_apply",
                    "reason": "Apply pending context patches explicitly",
                    "args": {"path": path},
                }
            ],
        },
        indent=2,
    )


@mcp.tool()
def context_patch_apply(
    path: str,
    patch_ids: list[str] | None = None,
    dry_run: bool = False,
) -> str:
    """Apply pending context patches to CLAUDE.md managed sections.

    By default applies all pending patches. Pass patch_ids to apply specific ones.

    Args:
        path: Project root path.
        patch_ids: Specific patch IDs to apply. If None, applies all pending.
        dry_run: Preview changes without writing (default False).
    """
    from lintgate.context_bootstrap import ContextPatch, apply_context_patch, generate_context_patch
    from lintgate.controlplane.session_memory import get_or_create_session, save_session

    project_root = _validate_project_root(path)
    session = get_or_create_session(project_root)

    pending = [p for p in session.pending_patches if p.get("status", "pending") == "pending"]

    if patch_ids is not None:
        pending = [p for p in pending if p.get("patch_id") in patch_ids]

    if not pending:
        return json.dumps({"applied": 0, "message": "No matching pending patches."}, indent=2)

    results = []
    for p_dict in pending:
        patch = ContextPatch.from_dict(p_dict)

        # Rebuild patch from latest on-disk CLAUDE.md before applying.
        # This prevents stale patch.new_content from clobbering earlier
        # patches when multiple pending patches target the same section.
        refreshed = generate_context_patch(
            project_root,
            trigger=patch.trigger,
            evidence=patch.evidence,
        )
        if refreshed is None:
            # No-op means already reflected or not applicable anymore.
            results.append(
                {
                    "patch_id": patch.patch_id,
                    "section_id": patch.section_id,
                    "applied": False,
                    "status": "no_op",
                    "diff_preview": None,
                }
            )
            if not dry_run:
                p_dict["status"] = "applied"
            continue

        refreshed.patch_id = patch.patch_id
        result = apply_context_patch(project_root, refreshed, dry_run=dry_run)
        results.append(
            {
                "patch_id": refreshed.patch_id,
                "section_id": refreshed.section_id,
                "applied": result.get("applied", False),
                "status": "applied" if result.get("applied", False) else "pending",
                "diff_preview": result.get("diff_preview"),
            }
        )
        if result.get("applied") and not dry_run:
            # Mark patch as applied in session
            p_dict["status"] = "applied"

    if not dry_run:
        save_session(session)

    return json.dumps(
        {
            "dry_run": dry_run,
            "applied": sum(1 for r in results if r["applied"]),
            "results": results,
        },
        indent=2,
    )


@mcp.tool()
def extract_theory_constraints(path: str) -> str:
    """Extract enforceable lint rules from CLAUDE.md/AGENTS.md prose directives.

    Analyzes DO NOT / MUST directives and proposes LINTGATE_FORBID_REGEX /
    LINTGATE_REQUIRE_REGEX rules. Deduplicates against existing rules.
    Returns proposed rules with copy-paste-ready lines for CLAUDE.md.
    """
    from lintgate.theory_extractor import extract_theory

    result = extract_theory(_validate_project_root(path))
    # Return just the enforceable rules for backward compat
    return json.dumps(result["enforceable_rules"], indent=2)


@mcp.tool()
def extract_project_theory(path: str) -> str:
    """Extract documented principles, philosophy, and patterns from project markdown files.

    WHEN TO USE: To understand a project's documented guidelines before making changes,
    or to check if changes align with documented principles.

    Scans all markdown documents in the codebase to identify: core principles,
    problem-solving approach, alignment criteria, architectural philosophy,
    anti-patterns, and key abstractions. Returns a structured profile with 6
    categories, each containing claims extracted from documentation with source
    references. Also includes enforceable lint rules as a subset.
    """
    from lintgate.theory_extractor import extract_theory

    return json.dumps(extract_theory(_validate_project_root(path)), indent=2)


@mcp.tool()
def build_theory_pack(
    path: str,
    include_full_profile: bool = False,
) -> str:
    """Build a compact summary of project principles for quick reference.

    Returns a two-level payload:
    - Summary (~500-1500 tokens): enforceable rules, principle summaries, anti-pattern list.
    - Full detail: complete documented claims for deeper lookup
      (only included when include_full_profile=true).

    Use this instead of extract_project_theory when you need a token-efficient
    overview for ongoing work.
    """
    from lintgate.theory_extractor import build_theory_pack as _build

    return json.dumps(
        _build(
            _validate_project_root(path),
            include_full_profile=include_full_profile,
        ),
        indent=2,
    )


@mcp.tool()
def get_theory_context(
    path: str,
    facet: str | None = None,
    keywords: list[str] | None = None,
    max_claims: int = 5,
) -> str:
    """Look up specific documented project principles by topic or keywords.

    WHEN TO USE: When you need deeper reasoning about a specific issue or design
    decision. Returns the most relevant documented principles matched by
    category and/or keyword overlap.

    Args:
        path: Project root path.
        facet: Optional category filter (core_theory, problem_solving,
            alignment, architecture, anti_patterns, abstractions).
        keywords: Optional keywords to match against principle text.
        max_claims: Max principles to return (default 5).
            Must be > 0.
    """
    if max_claims <= 0:
        raise ValueError("max_claims must be > 0")

    from lintgate.theory_extractor import get_theory_context as _get

    return json.dumps(
        _get(_validate_project_root(path), facet=facet, keywords=keywords, max_claims=max_claims),
        indent=2,
    )


# ─── Dependency Health Tools ─────────────────────────────────────────────


@mcp.tool()
def dep_health_check(path: str) -> str:
    """Run a comprehensive dependency health audit for a project.

    Checks virtual environment, lockfile presence and freshness,
    .python-version, conflicting package managers, manifest quality,
    and dependency churn patterns.

    Returns a structured report with issues and suggestions.
    """
    from lintgate.dependency_health import full_dependency_health

    project_root = _validate_project_root(path)
    return json.dumps(full_dependency_health(project_root), indent=2)


@mcp.tool()
def dep_sync(
    path: str,
    create_venv: bool = False,
    lock: bool = False,
) -> str:
    """Check dependency sync status and optionally create venv or lockfile.

    By default, only reports status. Use flags to take action:
    - create_venv: Create a .venv with `uv venv .venv`
    - lock: Generate/refresh lockfile with `uv lock`

    Returns sync status and any actions taken.
    """
    import shutil
    import subprocess

    project_root = _validate_project_root(path)
    root = Path(project_root)
    result: dict[str, Any] = {"project": project_root, "actions": []}

    # Check current state
    from lintgate.dependency_health import full_dependency_health

    health = full_dependency_health(project_root)
    result["health_before"] = health["summary"]

    uv_path = shutil.which("uv")
    if not uv_path:
        result["error"] = (
            "uv not found in PATH — install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
        )
        return json.dumps(result, indent=2)

    if create_venv:
        venv_path = root / ".venv"
        if venv_path.exists():
            result["actions"].append(
                {"action": "create_venv", "status": "skipped", "reason": ".venv already exists"}
            )
        else:
            try:
                proc = subprocess.run(
                    [uv_path, "venv", ".venv"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=project_root,
                )
                result["actions"].append(
                    {
                        "action": "create_venv",
                        "status": "ok" if proc.returncode == 0 else "error",
                        "returncode": proc.returncode,
                        "stderr": proc.stderr.strip()[-500:] if proc.stderr else None,
                    }
                )
            except subprocess.TimeoutExpired:
                result["actions"].append({"action": "create_venv", "status": "timeout"})

    if lock:
        try:
            proc = subprocess.run(
                [uv_path, "lock"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=project_root,
            )
            result["actions"].append(
                {
                    "action": "lock",
                    "status": "ok" if proc.returncode == 0 else "error",
                    "returncode": proc.returncode,
                    "stderr": proc.stderr.strip()[-500:] if proc.stderr else None,
                }
            )
        except subprocess.TimeoutExpired:
            result["actions"].append({"action": "lock", "status": "timeout"})

    # Re-check health after actions
    if create_venv or lock:
        health_after = full_dependency_health(project_root)
        result["health_after"] = health_after["summary"]

    return json.dumps(result, indent=2)


# ─── Remediation Tools ───────────────────────────────────────────────────


@mcp.tool()
def lint_fix(
    files: list[str] | None = None,
    path: str | None = None,
    dry_run: bool = True,
    safe_only: bool = True,
) -> str:
    """Auto-fix safe lint issues found by lint_files or lint_project.

    WHEN TO USE: After lint_files/lint_project reports fixable issues.
    Applies ruff's safe auto-fix rules (formatting, import sorting, simple corrections).

    Example: lint_fix(path="/my/project", dry_run=False)

    Default is dry_run=True which previews changes without modifying files.
    Set dry_run=False to apply fixes.

    Args:
        files: Specific files to fix. If None, uses path to fix entire project.
        path: Project root (required if files is None).
        dry_run: Preview changes without applying (default True).
        safe_only: Only apply ruff's safe fix rules (default True).
    """
    from lintgate.lint_fixer import run_safe_fixes

    if not files and not path:
        raise ValueError("Either files or path must be provided")

    if path:
        project_root = _validate_project_root(path)
    else:
        project_root = os.path.dirname(os.path.abspath(files[0]))

    # Resolve files
    if files:
        existing, missing = _resolve_files(files, project_root)
        target_files = existing
    else:
        target_files = _collect_python_files(project_root)

    if not target_files:
        return _json_dumps({"error": "No Python files found", "dry_run": dry_run}, "standard")

    result = run_safe_fixes(
        files=target_files,
        project_root=project_root,
        dry_run=dry_run,
        safe_only=safe_only,
    )

    return _json_dumps(result.to_dict(), "standard")


# ─── ControlPlane Tools ──────────────────────────────────────────────────


@mcp.tool()
def controlplane_run(
    path: str,
    channels: str | None = None,
    strictness: Literal["relaxed", "normal", "strict"] = "normal",
) -> str:
    """Run a comprehensive project health check across multiple dimensions.

    WHEN TO USE: At the start of a session to understand project state, or after
    significant changes. This is the most thorough single analysis available.
    Works without any configuration file.

    Example: controlplane_run(path="/my/project")

    Runs 5 independent analysis channels in parallel: lint (code quality),
    tests (coverage and health), deps (dependency issues), git (hygiene),
    behavior (patterns across sessions). Returns compact findings with a run_id.
    Use controlplane_get_details(run_id) to drill into specific findings.

    Args:
        path: Project root path.
        channels: Comma-separated channel list (default: all). Options: lint,tests,deps,git,behavior
        strictness: Strictness level for analysis.
    """
    from lintgate.channels.dependency_channel import DependencyChannel
    from lintgate.channels.git_channel import GitChannel
    from lintgate.channels.lint_channel import LintChannel
    from lintgate.channels.test_channel import TestChannel
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.runtime import run_mesh
    from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
    from lintgate.types import ChangeClassification

    project_root = _validate_project_root(path)

    # Load or build config
    cp_config = load_controlplane_config(project_root)
    if not cp_config:
        cp_config = ControlPlaneConfig(
            enabled=True,
            latency_budget_ms=30000,  # MCP gets more time
        )

    # Build channel registry
    from lintgate.channels.behavior_channel import BehaviorChannel

    channel_registry = {
        "lint": LintChannel(),
        "tests": TestChannel(),
        "deps": DependencyChannel(),
        "git": GitChannel(),
        "behavior": BehaviorChannel(),
    }

    # Select requested channels
    requested = [c.strip() for c in (channels or "lint,tests,deps,git,behavior").split(",")]
    active_channels = []
    unknown = []
    for name in requested:
        if name in channel_registry:
            active_channels.append(channel_registry[name])
        else:
            unknown.append(name)

    if not active_channels:
        raise ValueError(f"No valid channels. Unknown: {unknown}")

    # Discover Python files for the event
    py_files = _collect_python_files(project_root)

    # Build an explicit synthetic classification for full-project MCP audits.
    # Channels use this to decide relevance; MCP runs should exercise the
    # requested channels even without a concrete hook event.
    files_for_event = py_files[:50]
    change_classification = ChangeClassification(
        files_changed=files_for_event,
        files_by_language={"python": files_for_event} if files_for_event else {},
        change_kind="logic",
        risk_level="structural" if len(files_for_event) > 1 else "moderate",
        tool_name="controlplane_run",
    )

    # Build event
    event = SupervisionEvent(
        surface="mcp",
        project_root=project_root,
        tool_name="controlplane_run",
        files_changed=files_for_event,
        change_classification=change_classification,
        raw_input={"strictness": strictness, "requested_channels": requested},
    )

    # Session memory: wire into MCP path for behavior channel
    session = None
    if cp_config.session_memory:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import get_or_create_session

            session = get_or_create_session(project_root, cp_config.session_max_age_hours)

    # Inject behavior compass into event for BehaviorChannel
    if session is not None and cp_config.channel_enabled("behavior"):
        event.raw_input["behavior_compass"] = session.behavior_compass

    # Inject global behavior priors if enabled
    if cp_config.global_memory_enabled and cp_config.channel_enabled("behavior"):
        with contextlib.suppress(Exception):
            from lintgate.controlplane.global_behavior_profile import (
                MIN_SAMPLE_SIZE,
                load_global_profile,
            )

            _gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
            if _gp.session_count >= MIN_SAMPLE_SIZE:
                event.raw_input["behavior_global_priors"] = {
                    "enabled": True,
                    "alpha": cp_config.global_memory_alpha,
                    "decay_horizon": cp_config.global_memory_decay_horizon,
                    "computed_bias_adjustments": _gp.computed_bias_adjustments,
                }

    # Run mesh
    mesh_result = run_mesh(event, cp_config, active_channels, session=session)

    # Build finding index for delta computation
    from lintgate.controlplane.reporter import build_finding_index, format_mesh_report_compact

    current_finding_index = build_finding_index(mesh_result)

    # Get previous finding index from session (if available)
    previous_finding_index = None
    if session is not None and session.snapshots:
        previous_finding_index = session.snapshots[-1].finding_index

    # Session memory: record snapshot after mesh
    if session is not None:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import (
                load_behavior_compass,
                record_mesh_run,
                save_behavior_compass,
                save_session,
            )

            record_mesh_run(session, mesh_result, finding_index=current_finding_index)

            # Persist behavior channel state deltas (cooldowns/escalation flags).
            for cr in mesh_result.channel_results:
                if cr.channel != "behavior":
                    continue
                delta = cr.metrics.get("behavior_compass_delta")
                if not isinstance(delta, dict):
                    continue
                compass = load_behavior_compass(session)
                compass.last_fired = delta.get("last_fired", compass.last_fired)
                compass.signal_fire_counts = delta.get(
                    "signal_fire_counts", compass.signal_fire_counts
                )
                compass.early_nudge_emitted = delta.get(
                    "early_nudge_emitted", compass.early_nudge_emitted
                )
                compass.pending_nudge_signals = delta.get(
                    "pending_nudge_signals", compass.pending_nudge_signals
                )
                compass.pending_nudge_precheck_count = delta.get(
                    "pending_nudge_precheck_count",
                    compass.pending_nudge_precheck_count,
                )
                compass.nudge_outcomes = delta.get("nudge_outcomes", compass.nudge_outcomes)
                save_behavior_compass(session, compass)

                # Persist global profile delta
                if cp_config.global_memory_enabled:
                    gp_delta = cr.metrics.get("global_profile_delta")
                    if isinstance(gp_delta, dict):
                        with contextlib.suppress(Exception):
                            from lintgate.controlplane.global_behavior_profile import (
                                apply_session_delta,
                                load_global_profile,
                                save_global_profile,
                            )

                            _gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
                            _sid = session.session_id if session else ""
                            apply_session_delta(_gp, gp_delta, session_id=_sid)
                            save_global_profile(_gp)

                break

            save_session(session)

    # Generate compact output
    compact = format_mesh_report_compact(
        mesh_result,
        cp_config,
        previous_finding_index=previous_finding_index,
    )

    # Save full details for drill-down
    with contextlib.suppress(Exception):
        from lintgate.state import save_controlplane_run

        full_details = _build_cp_full_details(mesh_result, current_finding_index)
        save_controlplane_run(compact["run_id"], full_details)

    if unknown:
        compact["unknown_channels"] = unknown

    # Remove finding_index from returned output (it's stored, not sent to agent)
    compact.pop("finding_index", None)

    # Include onboarding when not fully configured
    _onboarding = _build_onboarding_status(project_root)
    if _onboarding.get("config_state") != "config_enabled":
        compact["onboarding"] = _onboarding

    return _json_dumps(compact, output_mode="compact")


@mcp.tool()
def controlplane_get_details(
    run_id: str,
    channel: str | None = None,
    severity: str | None = None,
    max_issues: int = 10,
    sections: list[str] | None = None,
) -> str:
    """Drill into a previous ControlPlane run by run_id.

    WHEN TO USE: After controlplane_run returns findings. The compact output
    shows counts and summaries — use this to see full issue details, evidence,
    and suggested repairs.

    Example: controlplane_get_details(run_id="cp_abc123")

    Args:
        run_id: The run_id from a controlplane_run response.
        channel: Filter findings by channel (lint, tests, deps, git, behavior).
        severity: Filter by severity (blocking, warning, informational).
        max_issues: Maximum findings to return (default 10).
        sections: Which sections to include. Default: all.
            Options: "findings", "channel_details", "evidence", "repairs", "coherence"
    """
    from lintgate.state import load_controlplane_run

    details = load_controlplane_run(run_id)
    if details is None:
        raise ValueError(f"No ControlPlane run found with run_id: {run_id}")

    sections_set = set(
        sections or ["findings", "channel_details", "evidence", "repairs", "coherence"]
    )
    output: dict[str, Any] = {"run_id": run_id, "duration_ms": details.get("duration_ms", 0)}

    if "coherence" in sections_set:
        output["coherence"] = details.get("coherence", {})

    if "findings" in sections_set:
        all_findings = []
        for ch_name, ch_data in details.get("channels", {}).items():
            if channel and ch_name != channel:
                continue
            for f in ch_data.get("findings", []):
                if severity and f.get("severity") != severity:
                    continue
                f_copy = {**f, "channel": ch_name}
                all_findings.append(f_copy)
        output["total_matching"] = len(all_findings)
        output["findings"] = all_findings[:max_issues]
        if len(all_findings) > max_issues:
            output["truncated"] = len(all_findings) - max_issues

    if "channel_details" in sections_set:
        ch_details: dict[str, Any] = {}
        for ch_name, ch_data in details.get("channels", {}).items():
            if channel and ch_name != channel:
                continue
            ch_details[ch_name] = {
                "status": ch_data.get("status"),
                "severity": ch_data.get("severity"),
                "finding_count": len(ch_data.get("findings", [])),
                "duration_ms": ch_data.get("duration_ms"),
                "error": ch_data.get("error"),
            }
        output["channel_details"] = ch_details

    if "repairs" in sections_set:
        all_repairs = []
        for ch_name, ch_data in details.get("channels", {}).items():
            if channel and ch_name != channel:
                continue
            all_repairs.extend(ch_data.get("repairs", []))
        output["repairs"] = all_repairs

    if "evidence" in sections_set:
        evidence: dict[str, Any] = {}
        for ch_name, ch_data in details.get("channels", {}).items():
            if channel and ch_name != channel:
                continue
            metrics = ch_data.get("metrics", {})
            if metrics:
                evidence[ch_name] = metrics
        if evidence:
            output["evidence"] = evidence

    return _json_dumps(output)


@mcp.tool()
def controlplane_status(path: str | None = None) -> str:
    """Show ControlPlane status for a project.

    Shows whether ControlPlane is enabled, which channels are configured,
    and the current config settings.
    """
    from lintgate.config import load_controlplane_config

    project_root = _validate_project_root(path) if path else os.getcwd()

    status: dict[str, Any] = {
        "project": project_root,
    }

    cp_config = load_controlplane_config(project_root)
    if cp_config:
        status["controlplane_enabled"] = cp_config.enabled
        status["latency_budget_ms"] = cp_config.latency_budget_ms
        status["advisory_default"] = cp_config.advisory_default
        status["session_memory"] = cp_config.session_memory
        status["session_max_age_hours"] = cp_config.session_max_age_hours
        status["constraint_proposal_threshold"] = cp_config.constraint_proposal_threshold
        status["token_policy"] = {
            "hook_max_tokens": cp_config.token_policy.hook_max_tokens,
            "include_pass_details": cp_config.token_policy.include_pass_details,
        }
        status["channels"] = {
            name: {
                "enabled": ch.enabled,
                "blocking": ch.blocking,
                "timeout_ms": ch.timeout_ms,
            }
            for name, ch in cp_config.channels.items()
        }

        # Session status
        if cp_config.session_memory:
            with contextlib.suppress(Exception):
                from lintgate.controlplane.session_memory import load_session

                session = load_session(project_root)
                if session:
                    status["session"] = {
                        "session_id": session.session_id,
                        "runs": len(session.snapshots),
                        "coherence_trajectory": session.coherence_trajectory[-5:],
                        "pending_repairs": sum(
                            1 for v in session.repair_outcomes.values() if v == "pending"
                        ),
                        "proposed_constraints": len(session.proposed_constraints),
                        "active_proposals": sum(
                            1 for c in session.proposed_constraints if c.get("status") == "proposed"
                        ),
                    }
                else:
                    status["session"] = None

        # Config exists but CP disabled — surface onboarding
        if not cp_config.enabled:
            status["onboarding"] = _build_onboarding_status(project_root)
    else:
        status["controlplane_enabled"] = False  # backward compat
        status["note"] = "Add 'controlplane: enabled: true' to .claude/lintgate.yaml to enable"  # backward compat
        status["onboarding"] = _build_onboarding_status(project_root)

    # Available channels
    status["available_channels"] = {
        "lint": "Code quality (ruff, mypy, complexity, structure)",
        "tests": "Test coverage and health (impacted test detection, skeleton generation)",
        "deps": "Dependency health (lockfile, venv, manifest)",
        "git": "Git hygiene (large changes, lockfile freshness, sensitive files)",
        "behavior": "Behavioral drift signals (approach cycling, failure amnesia, brute force escalation)",
    }

    return json.dumps(status, indent=2)


@mcp.tool()
def controlplane_test_skeleton(
    path: str,
    target_file: str,
) -> str:
    """Generate a test skeleton for a source file.

    Uses AST analysis and test archetype matching to produce a pytest
    skeleton with appropriate test stubs, fixtures, and imports.

    Args:
        path: Project root path.
        target_file: Source file to generate tests for.
    """
    from lintgate.controlplane.skeleton_generator import generate_test_path, generate_test_skeleton

    project_root = _validate_project_root(path)

    # Resolve target file
    if not os.path.isabs(target_file):
        target_file = os.path.normpath(os.path.join(project_root, target_file))

    if not os.path.exists(target_file):
        raise ValueError(f"Source file not found: {target_file}")

    skeleton = generate_test_skeleton(target_file, project_root=project_root)
    test_path = generate_test_path(target_file, project_root)

    return json.dumps(
        {
            "source_file": target_file,
            "test_path": test_path,
            "skeleton": skeleton,
            "note": "Review and customize before saving. Use Write tool to create the file.",
        },
        indent=2,
    )


# ─── Session Memory Tools ────────────────────────────────────────────────


@mcp.tool()
def controlplane_report_repair(
    path: str,
    action_id: str,
    outcome: str = "applied",
) -> str:
    """Report the outcome of a proposed repair action.

    Call this after applying (or deciding to skip) a repair suggested
    by ControlPlane. Tracks outcomes in session memory for future
    improvement of repair proposals.

    Args:
        path: Project root path.
        action_id: The repair action ID from the controlplane report.
        outcome: One of 'applied', 'ignored', 'rejected'.
    """
    from lintgate.controlplane.session_memory import (
        get_or_create_session,
        report_repair_outcome,
        save_session,
    )

    project_root = _validate_project_root(path)
    valid_outcomes = {"applied", "ignored", "rejected"}
    if outcome not in valid_outcomes:
        raise ValueError(f"Invalid outcome '{outcome}'; expected one of: {sorted(valid_outcomes)}")

    session = get_or_create_session(project_root)
    report_repair_outcome(session, action_id, outcome)
    save_session(session)

    return json.dumps(
        {
            "action_id": action_id,
            "outcome": outcome,
            "session_id": session.session_id,
            "pending_repairs": sum(1 for v in session.repair_outcomes.values() if v == "pending"),
            "total_repairs_tracked": len(session.repair_outcomes),
        },
        indent=2,
    )


@mcp.tool()
def controlplane_agent_feedback(
    path: str,
    run_id: str | None = None,
    disagreement: str | None = None,
    accepted_constraints: list[str] | None = None,
    rejected_constraints: list[str] | None = None,
) -> str:
    """Provide agent feedback on ControlPlane findings or constraint proposals.

    Use this to:
    - Record disagreements with specific findings
    - Accept proposed constraints (they'll be tracked as accepted)
    - Reject proposed constraints (they won't be re-proposed)

    Args:
        path: Project root path.
        run_id: Optional run ID this feedback relates to.
        disagreement: Optional description of what the agent disagrees with.
        accepted_constraints: Pattern keys to accept (e.g. ["ruff|F821"]).
        rejected_constraints: Pattern keys to reject.
    """
    from lintgate.controlplane.constraint_proposer import update_constraint_status
    from lintgate.controlplane.session_memory import get_or_create_session, save_session

    project_root = _validate_project_root(path)
    session = get_or_create_session(project_root)

    actions_taken = []

    # Record disagreement
    if disagreement:
        session.agent_disagreements.append(
            {
                "run_id": run_id or "unknown",
                "disagreement": disagreement,
                "timestamp": time.time(),
            }
        )
        actions_taken.append(f"Recorded disagreement: {disagreement[:100]}")

    # Accept constraints
    accepted_rules: list[str] = []
    for key in accepted_constraints or []:
        if update_constraint_status(session, key, "accepted"):
            actions_taken.append(f"Accepted constraint: {key}")
            # Find the accepted rule text for patch generation
            for p in session.proposed_constraints:
                if p.get("pattern_key") == key and p.get("status") == "accepted":
                    rule_text = p.get("proposed_rule", "")
                    if rule_text:
                        accepted_rules.append(rule_text)
                    break
        else:
            actions_taken.append(f"Constraint not found: {key}")

    # Reject constraints
    for key in rejected_constraints or []:
        if update_constraint_status(session, key, "rejected"):
            actions_taken.append(f"Rejected constraint: {key}")
        else:
            actions_taken.append(f"Constraint not found: {key}")

    # Generate context patches for accepted constraints (living context)
    from lintgate.config import load_controlplane_config

    cp_config = load_controlplane_config(project_root)
    if cp_config and cp_config.inquiry.living_context and accepted_rules:
        from lintgate.context_bootstrap import generate_context_patch

        for rule_text in accepted_rules:
            patch = generate_context_patch(
                project_root,
                trigger="constraint_accepted",
                evidence={"rule": rule_text, "rationale": "Accepted via agent feedback"},
            )
            if patch is not None:
                session.pending_patches.append(patch.to_dict())
                actions_taken.append(f"Generated context patch: {patch.patch_id}")

    save_session(session)

    return json.dumps(
        {
            "session_id": session.session_id,
            "actions_taken": actions_taken,
            "total_disagreements": len(session.agent_disagreements),
            "proposed_constraints": len(session.proposed_constraints),
            "active_proposals": sum(
                1 for c in session.proposed_constraints if c.get("status") == "proposed"
            ),
        },
        indent=2,
    )


# ─── Remediation: Apply Repairs ──────────────────────────────────────────


@mcp.tool()
def controlplane_apply_repairs(
    path: str,
    action_ids: list[str] | None = None,
    safe_only: bool = True,
) -> str:
    """Execute proposed repair actions from a ControlPlane run.

    Only executes command-type repairs. Requires explicit invocation.

    Args:
        path: Project root path.
        action_ids: Specific action IDs to execute. If None, executes all safe pending repairs.
        safe_only: Only execute repairs marked as safe (default True).
    """
    import subprocess

    from lintgate.controlplane.session_memory import (
        get_or_create_session,
        report_repair_outcome,
        save_session,
    )

    project_root = _validate_project_root(path)
    session = get_or_create_session(project_root)

    # Collect pending repairs from the latest snapshot
    pending_repairs: list[dict[str, Any]] = []
    if session.snapshots:
        latest = session.snapshots[-1]
        for repair in latest.get("repairs", []):
            repair_id = repair.get("action_id", "")
            outcome = session.repair_outcomes.get(repair_id, "pending")
            if outcome != "pending":
                continue
            if action_ids and repair_id not in action_ids:
                continue
            if safe_only and not repair.get("safe", True):
                continue
            pending_repairs.append(repair)

    results: list[dict[str, Any]] = []
    for repair in pending_repairs:
        if repair.get("kind") != "command":
            results.append(
                {
                    "action_id": repair.get("action_id"),
                    "status": "skipped",
                    "reason": "not a command",
                }
            )
            continue

        payload = repair.get("payload", {})
        command = payload.get("command", "")
        cwd = payload.get("cwd", project_root)

        if not command:
            results.append(
                {
                    "action_id": repair.get("action_id"),
                    "status": "skipped",
                    "reason": "empty command",
                }
            )
            continue

        try:
            proc = subprocess.run(
                command.split(),
                capture_output=True,
                text=True,
                timeout=60,
                cwd=cwd,
            )
            status = "ok" if proc.returncode == 0 else "error"
            results.append(
                {
                    "action_id": repair.get("action_id"),
                    "command": command,
                    "status": status,
                    "returncode": proc.returncode,
                    "stderr": proc.stderr.strip()[-300:] if proc.stderr else None,
                }
            )
            report_repair_outcome(
                session, repair.get("action_id", ""), "applied" if status == "ok" else "ignored"
            )
        except subprocess.TimeoutExpired:
            results.append(
                {"action_id": repair.get("action_id"), "command": command, "status": "timeout"}
            )
        except OSError as e:
            results.append(
                {
                    "action_id": repair.get("action_id"),
                    "command": command,
                    "status": "error",
                    "error": str(e),
                }
            )

    save_session(session)

    return json.dumps(
        {
            "repairs_executed": len(results),
            "results": results,
            "pending_remaining": sum(1 for v in session.repair_outcomes.values() if v == "pending"),
        },
        indent=2,
    )


# ─── Telemetry Tools ────────────────────────────────────────────────────


@mcp.tool()
def telemetry_summary(
    path: str,
    period: str = "7d",
) -> str:
    """ROI dashboard: code quality improvement vs token cost.

    Aggregates lint run metrics over a time window to show:
    total runs, issues found, fix rate, avg duration, token estimates,
    tier distribution, and quality trend.

    Args:
        path: Project root path.
        period: Time window — "1d", "7d", "30d", or "all".
    """
    from lintgate.telemetry import compute_telemetry_summary

    project_root = _validate_project_root(path)
    summary = compute_telemetry_summary(project_root, period=period)
    return json.dumps(summary, indent=2)


@mcp.tool()
def behavior_precheck(
    path: str,
    planned_action: str,
    known_constraints: list[str] | None = None,
    prediction: str | None = None,
    prediction_type: str | None = None,
    prediction_value: str | int | None = None,
) -> str:
    """Check a planned action against known constraints before executing it.

    WHEN TO USE: Before running Bash commands or making significant changes.
    State what you plan to do and what constraints you know about — this tool
    identifies coverage gaps, uncertainty zones, and similar past failures.

    Example: behavior_precheck(path="/my/project", planned_action="run pytest",
        known_constraints=["some tests may fail due to missing fixtures"])

    Args:
        path: Project root path.
        planned_action: Free text describing the planned action.
        known_constraints: Agent's self-reported constraints for this action.
        prediction: Optional free-text description of expected outcome.
        prediction_type: Type of prediction: "exit_code", "error_signature", or "stdout_contains".
        prediction_value: The expected value for the prediction.
    """
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.behavior_compass import (
        Prediction,
        PredictionExpectation,
        add_declared_hypothesis,
        compute_coverage,
        compute_prediction_accuracy,
        compute_uncertainty_zones,
        find_relevant_hypotheses,
        normalize_command_sig,
    )
    from lintgate.controlplane.session_memory import (
        get_or_create_session,
        load_behavior_compass,
        save_behavior_compass,
        save_session,
    )

    project_root = _validate_project_root(path)
    declared = known_constraints or []

    # Load config and session
    cp_config = load_controlplane_config(project_root)
    max_age = cp_config.session_max_age_hours if cp_config else 4.0
    session = get_or_create_session(project_root, max_age)
    compass = load_behavior_compass(session)

    # v2: Track precheck invocation
    compass.precheck_count_session += 1

    # Extract command sig from planned_action (best-effort)
    command_sig = normalize_command_sig(planned_action)

    # Find relevant hypotheses
    relevant = find_relevant_hypotheses(compass, command_sig, tool="Bash")

    # If no scoped hypotheses found, fall back to all active
    if not relevant:
        relevant = find_relevant_hypotheses(compass)

    # Agent-declared constraints: add as hypotheses if new
    for claim in declared:
        add_declared_hypothesis(compass, claim, command_sig)

    # Register prediction if provided and action involves Bash/execute
    _is_bash_action = any(
        kw in planned_action.lower()
        for kw in ("bash", "execute", "run", "command", "shell", "npm", "pip", "git", "make")
    )
    prediction_registered = False
    _valid_prediction_types = {"exit_code", "error_signature", "stdout_contains"}
    if (
        prediction
        and prediction_type
        and prediction_value is not None
        and _is_bash_action
        and prediction_type in _valid_prediction_types
        and command_sig
        and command_sig != "unknown:unknown"
    ):
        import uuid

        exp = PredictionExpectation(
            type=prediction_type,
            value=prediction_value,
        )
        # Link to most relevant hypothesis if available
        linked_hyp_id = relevant[0].id if relevant else None

        pred_obj = Prediction(
            prediction_id=uuid.uuid4().hex[:8],
            claim=prediction,
            expected=exp,
            declared_at_event=compass.event_counter,
            declared_sig=command_sig,
            linked_hypothesis_id=linked_hyp_id,
        )
        compass.pending_predictions.append(pred_obj)
        prediction_registered = True

    # Compute coverage gap
    matched_relevant_ids: set[str] = set()
    for claim in declared:
        # Check if any relevant hypothesis matches (keyword overlap)
        claim_words = set(claim.lower().split())
        for h in relevant:
            hyp_words = set(h.claim.lower().split())
            if len(claim_words & hyp_words) >= 2:
                matched_relevant_ids.add(h.id)
                break

    agent_matched = len(matched_relevant_ids)
    coverage_gap = max(0, len(relevant) - agent_matched)
    recall = agent_matched / len(relevant) if relevant else 1.0

    # Recompute coverage
    coverage = compute_coverage(compass)
    uncertainty = compute_uncertainty_zones(compass)

    # Find similar past failures (one-liner per failure for compact output)
    similar_failures = []
    for a in compass.approaches:
        if a.outcome == "failed":
            # Check if approach sig overlaps with planned action
            binary = command_sig.split(":")[0] if ":" in command_sig else ""
            approach_binary = a.approach_sig.split(":")[0] if ":" in a.approach_sig else ""
            if binary and binary == approach_binary:
                last_err = a.error_sigs[-1] if a.error_sigs else ""
                similar_failures.append(
                    {
                        "sig": a.approach_sig,
                        "count": a.event_count,
                        "error": last_err[:80],
                    }
                )

    # Build recommendation
    parts = []
    if coverage_gap > 0:
        parts.append(f"{coverage_gap} unverified constraint area{'s' if coverage_gap != 1 else ''}")
    if recall < 1.0:
        parts.append(f"{recall:.0%} prediction recall")
    if uncertainty:
        parts.append(f"{len(uncertainty)} uncertainty zone{'s' if len(uncertainty) != 1 else ''}")
    if similar_failures:
        parts.append(
            f"{len(similar_failures)} similar past failure{'s' if len(similar_failures) != 1 else ''}"
        )

    if parts:
        recommendation = (
            ". ".join(parts) + ". Consider researching uncertainty zones before acting."
        )
    else:
        recommendation = "Good constraint coverage. Proceed with awareness of known constraints."

    # Save compass updates (new declared hypotheses)
    save_behavior_compass(session, compass)
    save_session(session)

    # Build output
    output: dict[str, Any] = {
        "constraint_ledger": [
            {"claim": h.claim[:100], "confidence": round(h.confidence, 2), "source": h.source}
            for h in relevant[:8]
        ],
        "coverage": {
            "constraints_verified": coverage.constraints_verified,
            "agent_reported": len(declared),
            "relevant_hypotheses": len(relevant),
            "coverage_gap": coverage_gap,
            "prediction_recall": round(recall, 2),
        },
        "uncertainty_zones": uncertainty[:3],
        "similar_failures": similar_failures[:5],
        "recommendation": recommendation,
    }

    # First-session guidance when precheck_count_session was just incremented to 1
    if compass.precheck_count_session == 1:
        output["first_session_hint"] = (
            "First precheck this session — predictions and constraint tracking "
            "improve as you use behavior_precheck before taking actions. "
            "State your known constraints and register predictions for best results."
        )
        _bp_onboarding = _build_onboarding_status(project_root)
        if _bp_onboarding.get("config_state") != "config_enabled":
            output["onboarding"] = _bp_onboarding

    # Prediction tracking section
    pred_accuracy = compute_prediction_accuracy(compass)
    checked_count = len(
        [e for e in compass.prediction_log if e.get("status") in ("confirmed", "falsified")]
    )
    prediction_section: dict[str, Any] = {
        "pending_count": len(compass.pending_predictions),
        "checked_count": checked_count,
        "prediction_registered": prediction_registered,
    }
    if pred_accuracy is not None:
        prediction_section["accuracy"] = round(pred_accuracy, 2)
    else:
        prediction_section["accuracy"] = None
        if checked_count > 0:
            prediction_section["accuracy_note"] = (
                f"Need {5 - checked_count} more checked predictions for accuracy"
            )
    # Recent prediction outcomes (last 5)
    recent_outcomes = compass.prediction_log[-5:] if compass.prediction_log else []
    if recent_outcomes:
        prediction_section["recent_outcomes"] = [
            {"id": o.get("prediction_id", "?"), "status": o.get("status", "?")}
            for o in recent_outcomes
        ]
    output["prediction_tracking"] = prediction_section

    next_actions = []
    if coverage_gap > 0 or recall < 0.5:
        next_actions.append(
            {
                "tool": "behavior_precheck",
                "reason": "Re-run after researching uncertainty zones",
                "priority": 1,
            }
        )

    if next_actions:
        output["next_actions"] = next_actions

    return _json_dumps(output)


# ── Global Memory MCP Tools ─────────────────────────────────────────────


@mcp.tool()
def global_memory_status(path: str) -> str:
    """Show cross-session behavioral analysis status.

    Returns session count, learned patterns, calibration settings,
    and computed bias adjustments from accumulated behavioral data.

    Args:
        path: Project root path.
    """
    from lintgate.config import load_controlplane_config

    project_root = os.path.abspath(path)
    cp_config = load_controlplane_config(project_root)
    if cp_config is None:
        return _json_dumps({"error": "ControlPlane not configured"})

    from lintgate.controlplane.global_behavior_profile import (
        GLOBAL_PROFILE_PATH,
        load_global_profile,
    )

    profile = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)

    # Compute nudge acceptance rates
    nudge_rates: dict[str, Any] = {}
    for signal, outcomes in profile.nudge_outcomes.items():
        total = outcomes.get("accepted", 0) + outcomes.get("ignored", 0)
        if total > 0:
            nudge_rates[signal] = {
                "accepted": outcomes.get("accepted", 0),
                "ignored": outcomes.get("ignored", 0),
                "acceptance_rate": round(outcomes["accepted"] / total, 2),
            }

    # Normalize intent ratios
    total_intents = sum(profile.intent_ratios.values()) or 1
    normalized_intents = {
        k: round(v / total_intents, 3)
        for k, v in sorted(profile.intent_ratios.items(), key=lambda x: -x[1])
    }

    output: dict[str, Any] = {
        "enabled": cp_config.global_memory_enabled,
        "profile_path": str(GLOBAL_PROFILE_PATH),
        "session_count": profile.session_count,
        "updated_at": profile.updated_at,
        "signal_priors": profile.signal_priors,
        "intent_ratios_normalized": normalized_intents,
        "nudge_outcomes": nudge_rates,
        "computed_bias_adjustments": {
            k: round(v, 4) for k, v in profile.computed_bias_adjustments.items()
        },
        "alpha_config": {
            "initial": cp_config.global_memory_alpha,
            "decay_horizon": cp_config.global_memory_decay_horizon,
            "ttl_days": cp_config.global_memory_ttl_days,
        },
    }

    return _json_dumps(output)


@mcp.tool()
def global_memory_reset(path: str) -> str:
    """Reset the global behavior profile. Useful after major workflow changes.

    Args:
        path: Project root path.
    """
    from lintgate.controlplane.global_behavior_profile import (
        GLOBAL_PROFILE_PATH,
        GlobalBehaviorProfile,
        save_global_profile,
    )

    save_global_profile(GlobalBehaviorProfile())
    return _json_dumps(
        {
            "status": "reset",
            "profile_path": str(GLOBAL_PROFILE_PATH),
            "message": "Global behavior profile has been reset to empty state.",
        }
    )


# ─── Model Calibration ─────────────────────────────────────────────────


@mcp.tool()
def model_profile_status(
    path: str,
    model_id: str | None = None,
) -> str:
    """Show model calibration profile status.

    Returns the resolved model key, calibration status, signal risk vector,
    and confidence level. If model_id is None, returns all stored profiles.

    Args:
        path: Project root path.
        model_id: Optional model identifier (e.g., "claude-opus-4", "gpt-4o").
            If None, returns summary of all stored profiles.
    """
    from lintgate.controlplane.model_profiles import (
        load_profiles,
        resolve_model_key,
    )

    store = load_profiles()

    if model_id is None:
        # Summary of all stored profiles
        summaries = []
        for key, profile in store.profiles.items():
            status = "usable" if profile.is_usable() else (
                "stale" if profile.is_stale() else "low_confidence"
            )
            summaries.append({
                "model_key": key,
                "status": status,
                "confidence": profile.confidence,
                "probe_runs": profile.probe_runs,
                "telemetry_samples": profile.telemetry_samples,
                "signal_count": len(profile.signal_risk),
                "age_days": round((
                    __import__("time").time() - profile.updated_at
                ) / 86400, 1),
            })
        return _json_dumps({
            "profiles_count": len(summaries),
            "profiles": summaries,
            "next_actions": [
                "model_profile_probe_start(model_id='<model>') — "
                "calibrate a new model",
            ] if not summaries else [
                "model_profile_probe_start(model_id='<model>') — "
                "calibrate or recalibrate a model",
                "bootstrap_context_files(model_id='<model>') — "
                "generate model-aware bootstrap content",
            ],
        })

    # Specific model lookup
    canonical = resolve_model_key(model_id)
    if canonical is None:
        return _json_dumps({
            "model_id": model_id,
            "status": "unresolved",
            "message": (
                f"Cannot resolve model identifier {model_id!r}. "
                "Expected format: 'claude-opus-4', 'gpt-4o', "
                "or 'provider:model-name'."
            ),
            "next_actions": [
                "Provide a recognized model identifier.",
            ],
        })

    profile = store.profiles.get(canonical)
    if profile is None:
        return _json_dumps({
            "model_key": canonical,
            "status": "no_profile",
            "message": f"No calibration profile found for {canonical}.",
            "next_actions": [
                f"model_profile_probe_start(model_id='{model_id}') — "
                "run calibration probe (60-120 seconds)",
            ],
        })

    import time as _time

    status = "usable" if profile.is_usable() else (
        "stale" if profile.is_stale() else "low_confidence"
    )
    age_days = round((_time.time() - profile.updated_at) / 86400, 1)

    result: dict = {
        "model_key": canonical,
        "status": status,
        "confidence": profile.confidence,
        "probe_version": profile.probe_version,
        "probe_runs": profile.probe_runs,
        "telemetry_samples": profile.telemetry_samples,
        "age_days": age_days,
        "signal_risk": profile.signal_risk,
        "custom_anti_patterns_count": len(profile.custom_anti_patterns),
        "custom_dispositions_count": len(profile.custom_dispositions),
    }

    next_actions = []
    if status == "stale":
        next_actions.append(
            f"model_profile_probe_start(model_id='{model_id}') — "
            "recalibrate (profile is stale)"
        )
    elif status == "low_confidence":
        next_actions.append(
            f"model_profile_probe_start(model_id='{model_id}') — "
            "recalibrate (low confidence)"
        )
    if status == "usable":
        next_actions.append(
            f"bootstrap_context_files(model_id='{model_id}') — "
            "generate model-aware bootstrap content"
        )
    result["next_actions"] = next_actions
    return _json_dumps(result)


@mcp.tool()
def model_profile_probe_start(
    path: str,
    model_id: str,
    probe_set: str = "quick",
) -> str:
    """Start a model calibration probe.

    Returns 5 multiple-choice questions that reveal the model's behavioral
    tendencies (approach cycling, verification habits, etc.). Answer all
    questions and submit via model_profile_probe_submit.

    Args:
        path: Project root path.
        model_id: Model identifier (e.g., "claude-opus-4", "gpt-4o").
        probe_set: Probe question set. Currently only "quick" (5 questions).
    """
    from lintgate.controlplane.model_probe import (
        PROBE_VERSION,
        SUPPORTED_PROBE_SETS,
        get_probe_questions,
    )
    from lintgate.controlplane.model_profiles import (
        resolve_model_key,
    )

    canonical = resolve_model_key(model_id)
    if canonical is None:
        return _json_dumps({
            "error": f"Cannot resolve model identifier {model_id!r}.",
            "hint": (
                "Expected format: 'claude-opus-4', 'gpt-4o', "
                "or 'provider:model-name'."
            ),
        })

    try:
        questions = get_probe_questions(probe_set)
    except ValueError as e:
        return _json_dumps({
            "error": str(e),
            "supported_probe_sets": sorted(SUPPORTED_PROBE_SETS),
        })
    probe_set = probe_set.strip().lower()

    # Check for existing profile
    from lintgate.controlplane.model_profiles import get_profile

    existing = get_profile(model_id)
    existing_info = None
    if existing is not None:
        import time as _time

        existing_info = {
            "confidence": existing.confidence,
            "probe_runs": existing.probe_runs,
            "age_days": round(
                (_time.time() - existing.updated_at) / 86400, 1
            ),
            "status": "usable" if existing.is_usable() else (
                "stale" if existing.is_stale() else "low_confidence"
            ),
        }

    return _json_dumps({
        "model_key": canonical,
        "probe_version": f"v{PROBE_VERSION}",
        "probe_set": probe_set,
        "question_count": len(questions),
        "questions": questions,
        "answer_schema": {
            "format": {"question_id": "choice_letter"},
            "example": {questions[0]["id"]: "B"},
            "minimum_answers": 3,
        },
        "existing_profile": existing_info,
        "eta": "60-120 seconds",
        "next_actions": [
            "Answer each question with a letter (A-D), then call "
            f"model_profile_probe_submit(model_id='{model_id}', "
            "answers={{...}})",
        ],
    })


@mcp.tool()
def model_profile_probe_submit(
    path: str,
    model_id: str,
    answers: dict[str, str] | None = None,
    probe_version: str = "v1",
) -> str:
    """Submit answers to a model calibration probe.

    Scores the responses deterministically into a signal_risk vector,
    derives model-specific anti-patterns and guardrail dispositions,
    and persists the profile for future bootstrap use.

    Args:
        path: Project root path.
        model_id: Model identifier (e.g., "claude-opus-4").
        answers: Question responses as {question_id: choice_letter}.
            Minimum 3 answers required. Example:
            {"q1_failure_response": "B", "q2_verification_habits": "A"}
        probe_version: Probe version string (default "v1").
    """
    from lintgate.controlplane.model_probe import (
        PROBE_VERSION,
        build_profile_from_probe,
        get_probe_questions,
    )
    from lintgate.controlplane.model_profiles import (
        get_profile,
        resolve_model_key,
        upsert_profile,
    )

    # Validate probe version
    expected_version = f"v{PROBE_VERSION}"
    if probe_version != expected_version:
        return _json_dumps({
            "error": f"Unknown probe version: {probe_version!r}",
            "expected": expected_version,
            "hint": "Run model_profile_probe_start to get current questions.",
        })

    # Validate model key
    canonical = resolve_model_key(model_id)
    if canonical is None:
        return _json_dumps({
            "error": f"Cannot resolve model identifier {model_id!r}.",
        })

    # Validate answers
    if not answers:
        return _json_dumps({
            "error": "No answers provided.",
            "hint": "Provide answers as {question_id: choice_letter}.",
        })

    valid_ids = {q["id"] for q in get_probe_questions()}
    invalid_ids = set(answers.keys()) - valid_ids
    if invalid_ids:
        return _json_dumps({
            "error": f"Unknown question IDs: {sorted(invalid_ids)}",
            "valid_ids": sorted(valid_ids),
        })

    if len(answers) < 3:
        return _json_dumps({
            "error": (
                f"Minimum 3 answers required, got {len(answers)}. "
                "Answer more questions for a usable profile."
            ),
        })

    # Score and build profile
    try:
        profile = build_profile_from_probe(model_id, answers)
    except ValueError as e:
        return _json_dumps({"error": str(e)})

    # Preserve run history on recalibration.
    existing = get_profile(model_id)
    if existing is not None:
        profile.created_at = existing.created_at
        profile.probe_runs = max(existing.probe_runs, 1) + 1
        profile.stale_after_days = existing.stale_after_days

    # Persist
    upsert_profile(profile)

    status = "usable" if profile.is_usable() else "low_confidence"

    next_actions = []
    if status == "usable":
        next_actions.append(
            f"bootstrap_context_files(model_id='{model_id}') — "
            "generate model-aware bootstrap content"
        )
    else:
        next_actions.append(
            "Answer more questions to increase confidence above 0.55 "
            "threshold."
        )
    next_actions.append(
        f"model_profile_status(model_id='{model_id}') — "
        "view full profile details"
    )

    return _json_dumps({
        "model_key": canonical,
        "status": status,
        "confidence": profile.confidence,
        "probe_runs": profile.probe_runs,
        "signal_risk": profile.signal_risk,
        "custom_anti_patterns": profile.custom_anti_patterns,
        "custom_dispositions": profile.custom_dispositions,
        "answers_submitted": len(answers),
        "message": (
            f"Profile created for {canonical} with confidence "
            f"{profile.confidence:.2f}."
        ),
        "next_actions": next_actions,
    })


# ─── Entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
