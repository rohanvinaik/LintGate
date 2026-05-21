#!/usr/bin/env python3
"""Lint runner — standalone lint execution for LintGate.

Subcommands:
    files     Lint specific files (maps to MCP lint_files)
    project   Lint entire project (maps to MCP lint_project)
    details   Load a prior run's details (maps to MCP lint_get_details)
    status    Full lint status report (maps to MCP lint_status)
    audit     Audit tool versions (maps to MCP audit_tool_versions)
    fix       Auto-fix safe issues (maps to MCP lint_fix)

Usage:
    python scripts/lint_run.py . files --files src/main.py src/utils.py
    python scripts/lint_run.py . project --tier 2
    python scripts/lint_run.py . details --run-id abc123 --severity blocking
    python scripts/lint_run.py . status
    python scripts/lint_run.py . audit --auto-fix
    python scripts/lint_run.py . fix --no-dry-run
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from lintgate.lint_helpers import _missing_tool_hints  # noqa: E402
from scripts._common import (  # noqa: E402
    collect_python_files,
    emit,
    emit_error,
    resolve_files,
    validate_project_root,
)

_VALID_STRICTNESS = {"relaxed", "normal", "strict"}

TIER_LINTERS: dict[int, list[str]] = {
    0: ["ruff"],
    1: ["ruff"],
    2: ["ruff", "mypy"],
    3: ["ruff", "mypy", "bandit", "vulture", "radon"],
}


# ── Core compute ─────────────────────────────────────────────────────────


def _run_lint(
    files: list[str],
    project_root: str,
    tier: int = 2,
    strictness: str = "normal",
) -> dict:
    """Core lint execution. Returns the full result dict matching MCP contract."""
    from lintgate.config import load_config
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import (
        generate_run_id,
        log_metric,
        save_run,
        save_run_details,
    )
    from lintgate.types import LintTier

    if strictness not in _VALID_STRICTNESS:
        strictness = "normal"
    tier = max(0, min(3, tier))

    start = time.perf_counter()
    config = load_config(project_root)
    registry = build_registry(config)

    linter_names = TIER_LINTERS[tier]
    lint_tier = LintTier(
        name=f"tier_{tier}_manual",
        linters=linter_names,
        files=files,
        reason=f"Manual invocation (tier {tier})",
        strictness=strictness,
    )

    linter_results = run_linters(lint_tier, config, registry, timeout_ms=30000)
    aggregated = aggregate_results(
        linter_results, config,
        tier_name=lint_tier.name, tier_reason=lint_tier.reason,
    )

    elapsed_ms = (time.perf_counter() - start) * 1000
    run_id = generate_run_id()

    with contextlib.suppress(Exception):
        save_run(project_root, aggregated)

    result = {
        "run_id": run_id,
        "tier": lint_tier.name,
        "project": project_root,
        "files_linted": len(files),
        "duration_ms": round(elapsed_ms, 1),
        "blocking_count": len(aggregated.blocking),
        "warning_count": len(aggregated.warnings),
        "info_count": len(aggregated.informational),
        "issue_count": aggregated.metrics.get("total_issues", 0),
        "fixable": aggregated.metrics.get("fixable_count", 0),
        "linters_run": aggregated.metrics.get("linters_run", 0),
        "blocking_issues": [issue.to_dict() for issue in aggregated.blocking],
        "warning_issues": [issue.to_dict() for issue in aggregated.warnings],
        "info_issues": [issue.to_dict() for issue in aggregated.informational],
    }

    with contextlib.suppress(Exception):
        save_run_details(run_id, result)

    with contextlib.suppress(Exception):
        log_metric({
            "event": "lint_run",
            "project": project_root,
            "tier": lint_tier.name,
            "files_count": len(files),
            "blocking_count": len(aggregated.blocking),
            "duration_ms": round(elapsed_ms, 1),
        })

    return result


def _load_project_baseline(project_root: str) -> dict:
    """Load the baseline from the last full project lint run."""
    from lintgate.state import load_last_run

    last_run = load_last_run(project_root)
    if last_run is None:
        return {"state": "unknown", "last_full_run": None}

    age_s = time.time() - last_run.get("timestamp", 0)
    return {
        "state": "cached",
        "pre_existing_blocking": last_run.get("blocking_count", 0),
        "pre_existing_warnings": last_run.get("warning_count", 0),
        "pre_existing_total": last_run.get("total_issues", 0),
        "last_full_run_age_s": round(age_s, 0),
        "baseline_tier": last_run.get("tier", ""),
    }


def _build_surgical_result(
    result: dict,
    existing: list[str],
    project_root: str,
    blocking: int,
    issues: int,
) -> dict:
    """Reshape a flat lint result into edit_scope + baseline for surgical mode."""
    rel_files = [os.path.relpath(f, project_root) for f in existing]
    verdict = "clean" if issues == 0 else "findings"
    baseline = _load_project_baseline(project_root)

    baseline_total = (
        baseline.get("pre_existing_total", 0)
        if baseline.get("state") == "cached" else None
    )
    delta = (issues - baseline_total) if baseline_total is not None else None

    return {
        "edit_scope": {
            "files": rel_files,
            "verdict": verdict,
            "issue_count": issues,
            "blocking_count": blocking,
            "findings": result.get("blocking_issues", [])[:10],
        },
        "baseline": {**baseline, "delta_from_edit": delta},
        "run_id": result.get("run_id", ""),
    }


def _update_refactor_state(files: list[str], project_root: str, issue_count: int) -> None:
    """Refactor state integration — update per-file findings."""
    with contextlib.suppress(Exception):
        from lintgate.refactor_state import update_file_findings
        for f in files:
            rel = os.path.relpath(f, project_root)
            update_file_findings(project_root, rel, issue_count)


def _build_run_next_actions(path: str, result: dict) -> list[dict]:
    """Next-action hints when a run has blockers or fixables."""
    next_actions = []
    if result.get("blocking_count", 0) > 0:
        next_actions.append({
            "tool": "lint_fix",
            "args": {"path": path},
            "reason": "Auto-fix safe issues",
        })
    if result.get("fixable", 0) > 0:
        next_actions.append({
            "tool": "lint_fix",
            "args": {"path": path, "dry_run": True},
            "reason": f"{result['fixable']} auto-fixable issues",
        })
    return next_actions


# ── Subcommands ─────────────────────────────────────────────────────────


def cmd_files(args):
    """Lint specific files — MCP lint_files."""
    if args.tier not in (0, 1, 2, 3):
        emit_error(f"Invalid tier {args.tier}; expected 0,1,2,3")
    if not args.files:
        emit_error("No files specified")

    if args.project_root:
        project_root = validate_project_root(args.project_root)
    else:
        project_root = os.path.dirname(os.path.abspath(args.files[0]))

    existing, missing = resolve_files(args.files, project_root)
    if not existing:
        emit_error(f"No specified files exist. Missing: {missing}")

    result = _run_lint(existing, project_root, args.tier, args.strictness)
    if missing:
        result["missing_files"] = missing

    _update_refactor_state(existing, project_root, result.get("issue_count", 0))

    blocking = result.get("blocking_count", 0)
    issues = result.get("issue_count", 0)

    if args.scope == "surgical":
        result = _build_surgical_result(result, existing, project_root, blocking, issues)
        verdict = "clean" if issues == 0 else f"{blocking} blocking, {issues - blocking} warnings"
        summary = f"edit_scope: {verdict}."
    else:
        summary = f"{issues} issues found in {len(existing)} files. {blocking} blocking."

    next_actions = _build_run_next_actions(args.path, result) if args.scope != "surgical" else None
    emit(result, "lint_files", project_root, summary,
         run_id=result.get("run_id", ""), next_actions=next_actions or None)


def cmd_project(args):
    """Lint whole project — MCP lint_project."""
    if args.tier not in (0, 1, 2, 3):
        emit_error(f"Invalid tier {args.tier}; expected 0,1,2,3")
    project_root = validate_project_root(args.path)

    py_files = collect_python_files(project_root)
    if not py_files:
        emit_error(f"No Python files found under: {project_root}")

    result = _run_lint(py_files, project_root, args.tier, args.strictness)
    result["total_python_files"] = len(py_files)

    blocking = result.get("blocking_count", 0)
    issues = result.get("issue_count", 0)
    summary = f"{issues} issues across {len(py_files)} files. {blocking} blocking."
    next_actions = _build_run_next_actions(args.path, result)
    emit(result, "lint_project", project_root, summary,
         run_id=result.get("run_id", ""), next_actions=next_actions or None)


def cmd_details(args):
    """Load run details — MCP lint_get_details."""
    from lintgate.state import load_run_details

    details = load_run_details(args.run_id)
    if details is None:
        emit_error(f"No lint run found with run_id: {args.run_id}")

    valid_severities = {"blocking", "warning", "informational", None}
    severity = args.severity if args.severity else None
    if severity not in valid_severities:
        emit_error(f"Invalid severity '{severity}'")

    output: dict[str, Any] = {
        "run_id": args.run_id,
        "tier": details.get("tier", ""),
        "project": details.get("project", ""),
        "duration_ms": details.get("duration_ms", 0),
    }

    issues: list[dict[str, Any]] = []
    if severity is None or severity == "blocking":
        issues.extend(details.get("blocking_issues", []))
    if severity is None or severity == "warning":
        issues.extend(details.get("warning_issues", []))
    if severity is None or severity == "informational":
        issues.extend(details.get("info_issues", []))

    output["total_matching"] = len(issues)
    output["issues"] = issues[:args.max_issues]
    if len(issues) > args.max_issues:
        output["truncated"] = len(issues) - args.max_issues

    if args.include_recurrence:
        output["recurrence"] = details.get("recurrence", {})

    if details.get("linter_diagnostics"):
        output["linter_diagnostics"] = details["linter_diagnostics"]

    sev_label = severity or "all"
    summary = f"Details for run {args.run_id}: {output['total_matching']} issues ({sev_label})."
    project_root = output.get("project") or os.getcwd()
    emit(output, "lint_get_details", project_root, summary, run_id=args.run_id)


def cmd_status(args):
    """Lint status — MCP lint_status."""
    from datetime import datetime as dt

    from lintgate.config import load_config
    from lintgate.context_guidance import build_context_guidance, summarize_context_guidance
    from lintgate.registry import build_registry
    from lintgate.state import METRICS_DIR, load_last_run, load_last_version_audit
    from lintgate.versioning import format_version_audit_summary

    project_root = validate_project_root(args.path) if args.path else os.getcwd()

    status: dict[str, Any] = {"version": "0.2.0"}

    config = load_config(project_root)
    registry = build_registry(config)

    from lintgate.lint_helpers import _linter_available
    linters_info = {}
    for name, linter in sorted(registry.items()):
        linters_info[name] = {
            "tier": linter.tier,
            "available": _linter_available(linter, project_root),
            "tool": linter.required_tool,
        }
    status["linters"] = linters_info
    status["linter_count"] = len(linters_info)
    status["missing_tools"] = _missing_tool_hints(project_root, registry)

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

    try:
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

    # Surface onboarding status when ControlPlane is not fully configured
    with contextlib.suppress(Exception):
        from mcp_server import _build_onboarding_status
        _onboarding = _build_onboarding_status(project_root)
        if _onboarding.get("config_state") != "config_enabled":
            status["onboarding"] = _onboarding

    missing = len(status.get("missing_tools", []))
    linter_count = status.get("linter_count", 0)
    last = status.get("last_run")
    last_info = f"run_id={last.get('run_id', '?')}" if last else "none"
    summary = (
        f"LintGate v{status.get('version', '?')}: {linter_count} linters, "
        f"{missing} missing tools. Last run: {last_info}."
    )
    emit(status, "lint_status", project_root, summary)


def cmd_audit(args):
    """Audit tool versions — MCP audit_tool_versions."""
    from lintgate.config import load_config
    from lintgate.state import log_version_event, save_version_audit
    from lintgate.versioning import format_version_audit_summary, run_version_audit

    project_root = validate_project_root(args.path)
    config = load_config(project_root)

    audit = run_version_audit(
        project_root,
        config_requirements=config.tool_version_requirements,
        auto_fix=args.auto_fix,
        verify_after_fix=args.verify_after_fix,
    )
    summary_data = format_version_audit_summary(audit)

    with contextlib.suppress(Exception):
        save_version_audit(project_root, audit)
    with contextlib.suppress(Exception):
        log_version_event({
            "event": "audit_tool_versions",
            "project": project_root,
            "auto_fix": args.auto_fix,
            "issue_count": summary_data.get("issue_count", 0) if isinstance(summary_data, dict) else 0,
            "post_fix_issue_count": (
                summary_data.get("post_fix_issue_count")
                if isinstance(summary_data, dict) else None
            ),
        })

    result = {"summary": summary_data, **audit}
    issue_count = summary_data.get("issue_count", 0) if isinstance(summary_data, dict) else 0
    fix_info = f" auto_fix={args.auto_fix}" if args.auto_fix else ""
    summary = f"Version audit: {issue_count} issues found.{fix_info}"
    emit(result, "audit_tool_versions", project_root, summary)


def cmd_fix(args):
    """Auto-fix safe issues — MCP lint_fix."""
    from lintgate.lint_fixer import run_safe_fixes

    if not args.files and not args.path:
        emit_error("Either files or path must be provided")

    if args.path:
        project_root = validate_project_root(args.path)
    else:
        project_root = os.path.dirname(os.path.abspath(args.files[0]))

    if args.files:
        existing, _ = resolve_files(args.files, project_root)
        target_files = existing
    else:
        target_files = collect_python_files(project_root)

    if not target_files:
        # Preserve legacy contract: error key in the output
        err = {"error": "No Python files found", "dry_run": args.dry_run}
        emit(err, "lint_fix", project_root, "No Python files found")
        return

    result = run_safe_fixes(
        files=target_files,
        project_root=project_root,
        dry_run=args.dry_run,
        safe_only=args.safe_only,
    )
    rd = result.to_dict()
    fixed = rd.get("fixed_count", 0)
    summary = f"{fixed} fixes applied (dry_run={args.dry_run})."

    next_actions: list[dict] = []
    if args.dry_run and fixed > 0:
        next_actions.append({
            "tool": "lint_fix",
            "args": {"path": args.path or project_root, "dry_run": False},
            "reason": f"Apply {fixed} fixes (currently dry_run)",
        })
    elif not args.dry_run and fixed > 0:
        next_actions.append({
            "tool": "lint_files",
            "args": {"path": args.path or project_root},
            "reason": "Verify fixes didn't introduce new issues",
        })

    emit(rd, "lint_fix", project_root, summary, next_actions=next_actions or None)


# ── CLI ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(prog="lint_run", description="LintGate lint runner")
    parser.add_argument("path", help="Project root path (or '.' for cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_files = sub.add_parser("files", help="Lint specific files")
    p_files.add_argument("--files", nargs="+", required=True)
    p_files.add_argument("--tier", type=int, default=2)
    p_files.add_argument("--strictness", default="normal", choices=["relaxed", "normal", "strict"])
    p_files.add_argument("--project-root", default=None)
    p_files.add_argument("--scope", default="compact", choices=["compact", "surgical"])

    p_project = sub.add_parser("project", help="Lint whole project")
    p_project.add_argument("--tier", type=int, default=2)
    p_project.add_argument("--strictness", default="normal", choices=["relaxed", "normal", "strict"])

    p_det = sub.add_parser("details", help="Get run details")
    p_det.add_argument("--run-id", required=True)
    p_det.add_argument("--severity", default=None)
    p_det.add_argument("--max-issues", type=int, default=10)
    p_det.add_argument("--include-recurrence", action="store_true")

    sub.add_parser("status", help="Lint status report")

    p_audit = sub.add_parser("audit", help="Audit tool versions")
    p_audit.add_argument("--auto-fix", action="store_true")
    p_audit.add_argument("--no-verify-after-fix", dest="verify_after_fix", action="store_false")
    p_audit.set_defaults(verify_after_fix=True)

    p_fix = sub.add_parser("fix", help="Auto-fix lint issues")
    p_fix.add_argument("--files", nargs="*", default=None)
    p_fix.add_argument("--dry-run", action="store_true", default=True)
    p_fix.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    p_fix.add_argument("--safe-only", action="store_true", default=True)
    p_fix.add_argument("--no-safe-only", dest="safe_only", action="store_false")

    args = parser.parse_args()
    dispatch = {
        "files": cmd_files,
        "project": cmd_project,
        "details": cmd_details,
        "status": cmd_status,
        "audit": cmd_audit,
        "fix": cmd_fix,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
