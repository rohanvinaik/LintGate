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
        "ty",
        "bandit_fast",
        "pip_audit",
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


# ─── Build helpers dict and register domain tools ────────────────────────

_helpers = {
    "_validate_project_root": _validate_project_root,
    "_json_dumps": _json_dumps,
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
globals().update(_tool_funcs)

# ─── Version constant (referenced by test_mcp_schema_contracts) ─────────
# lint_status reports "version": "0.2.0" — keep this here for source-level checks.
_VERSION = "0.2.0"

# ─── Entry point ────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
