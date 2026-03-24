#!/usr/bin/env python3
"""Lint runner — standalone lint execution for LintGate.

Usage:
    python scripts/lint_run.py . run --files src/main.py src/utils.py
    python scripts/lint_run.py . run --tier 2 --strictness normal
    python scripts/lint_run.py . project --tier 2
    python scripts/lint_run.py . fix --dry-run
    python scripts/lint_run.py . status
    python scripts/lint_run.py . details --run-id abc123 --severity blocking
"""
from __future__ import annotations

import argparse
import contextlib
import os
import sys
import time

# Add project root to path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, PROJECT_ROOT)

from scripts._common import (
    collect_python_files,
    emit,
    emit_error,
    resolve_files,
    save_analysis,
    validate_project_root,
)


# ── Lint pipeline (moved from mcp_server.py) ──────────────────────────


_VALID_STRICTNESS = {"relaxed", "normal", "strict"}
_VALID_OUTPUT_MODES = {"compact", "standard", "full"}

TIER_LINTERS: dict[int, list[str]] = {
    0: ["ruff"],
    1: ["ruff"],
    2: ["ruff", "mypy"],
    3: ["ruff", "mypy", "bandit", "vulture", "radon"],
}


def _run_lint(
    files: list[str],
    project_root: str,
    tier: int = 2,
    strictness: str = "normal",
    max_findings: int = 20,
) -> dict:
    """Core lint execution. Returns the full result dict."""
    from lintgate.config import load_config
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import (
        generate_run_id,
        load_last_run,
        save_run,
        save_run_details,
    )
    from lintgate.state import log_metric

    if strictness not in _VALID_STRICTNESS:
        strictness = "normal"
    tier = max(0, min(3, tier))

    start = time.perf_counter()

    config = load_config(project_root)
    registry = build_registry(config)

    from lintgate.types import LintTier

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

    # Persist state
    with contextlib.suppress(Exception):
        save_run(project_root, aggregated)

    # Build full details for disk
    full_details = {
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
        save_run_details(run_id, full_details)

    with contextlib.suppress(Exception):
        log_metric({
            "event": "lint_run",
            "project": project_root,
            "tier": lint_tier.name,
            "files_count": len(files),
            "blocking_count": len(aggregated.blocking),
            "duration_ms": round(elapsed_ms, 1),
        })

    return full_details


def _run_fix(files: list[str], project_root: str, dry_run: bool = True, safe_only: bool = True) -> dict:
    """Run auto-fix on files."""
    from lintgate.lint_fixer import run_safe_fixes

    result = run_safe_fixes(files=files, project_root=project_root, dry_run=dry_run, safe_only=safe_only)
    return result.to_dict()


def _get_status(project_root: str) -> dict:
    """Get lint status."""
    from lintgate.config import load_config
    from lintgate.registry import build_registry

    config = load_config(project_root)
    registry = build_registry(config)

    missing = [name for name, entry in registry.items() if not entry.available]
    return {
        "version": "latest",
        "linter_count": len(registry),
        "missing_tools": missing,
        "missing_count": len(missing),
    }


def _get_details(run_id: str, severity: str | None = None, max_issues: int = 10) -> dict:
    """Load run details and filter."""
    from lintgate.state import load_controlplane_run, load_run_details

    details = load_run_details(run_id)
    if details is None:
        details = load_controlplane_run(run_id)
    if details is None:
        # Try disk analysis files
        for name in ("lint_files", "lint_project", "lint_run"):
            path = os.path.join(os.getcwd(), ".lintgate", "analysis", name, f"{run_id}.json")
            if os.path.isfile(path):
                import json
                with open(path) as f:
                    details = json.loads(f.read())
                break
    if details is None:
        return {"error": f"Run {run_id} not found"}

    if severity:
        key = f"{severity}_issues"
        issues = details.get(key, [])
        return {
            "run_id": run_id,
            "severity": severity,
            "total_matching": len(issues),
            "issues": issues[:max_issues],
        }
    return details


# ── CLI ────────────────────────────────────────────────────────────────


def cmd_run(args):
    project_root = validate_project_root(args.path)
    if args.files:
        existing, missing = resolve_files(args.files, project_root)
        if not existing:
            emit_error("No valid Python files found")
    else:
        existing = collect_python_files(project_root)
        if not existing:
            emit_error("No Python files found in project")

    result = _run_lint(existing, project_root, args.tier, args.strictness, args.max_findings)

    blocking = result.get("blocking_count", 0)
    total = result.get("issue_count", 0)
    summary = f"{total} issues in {result['files_linted']} files. {blocking} blocking."

    # Top blockers inline
    blockers = result.get("blocking_issues", [])[:5]
    if blockers:
        lines = [summary, "", "Top blockers:"]
        for b in blockers:
            lines.append(f"  {b.get('kind', '?'):20s} {b.get('file', '?'):30s} {b.get('message', '')[:50]}")
        summary = "\n".join(lines)

    next_actions = []
    if blocking > 0:
        next_actions.append({"tool": "lint_fix", "args": {"path": args.path}, "reason": "Auto-fix safe issues"})
    if result.get("fixable", 0) > 0:
        next_actions.append({"tool": "lint_fix", "args": {"path": args.path, "dry_run": True}, "reason": f"{result['fixable']} auto-fixable issues"})

    emit(result, "lint_run", project_root, summary,
         run_id=result.get("run_id", ""), next_actions=next_actions,
         extra={"blocking": blocking, "files_linted": result["files_linted"]})


def cmd_fix(args):
    project_root = validate_project_root(args.path)
    if args.files:
        existing, _ = resolve_files(args.files, project_root)
    else:
        existing = collect_python_files(project_root)

    if not existing:
        emit_error("No Python files found")

    result = _run_fix(existing, project_root, dry_run=args.dry_run, safe_only=args.safe_only)
    fixed = result.get("fixed_count", 0)
    summary = f"{fixed} fixes {'previewed' if args.dry_run else 'applied'}."
    emit(result, "lint_fix", project_root, summary)


def cmd_status(args):
    project_root = validate_project_root(args.path)
    result = _get_status(project_root)
    missing = result["missing_count"]
    summary = f"{result['linter_count']} linters. {missing} missing."
    emit(result, "lint_status", project_root, summary)


def cmd_details(args):
    result = _get_details(args.run_id, args.severity, args.max_issues)
    if "error" in result:
        emit_error(result["error"])

    total = result.get("total_matching", 0)
    sev = args.severity or "all"
    summary = f"{total} {sev} findings for run {args.run_id}."
    emit(result, "lint_details", args.path or os.getcwd(), summary, run_id=args.run_id)


def main():
    parser = argparse.ArgumentParser(prog="lint_run", description="LintGate lint runner")
    parser.add_argument("path", help="Project root path")
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = sub.add_parser("run", help="Lint files or project")
    p_run.add_argument("--files", nargs="*", help="Specific files to lint")
    p_run.add_argument("--tier", type=int, default=2)
    p_run.add_argument("--strictness", default="normal", choices=["relaxed", "normal", "strict"])
    p_run.add_argument("--max-findings", type=int, default=20)

    # fix
    p_fix = sub.add_parser("fix", help="Auto-fix lint issues")
    p_fix.add_argument("--files", nargs="*")
    p_fix.add_argument("--dry-run", action="store_true", default=True)
    p_fix.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    p_fix.add_argument("--safe-only", action="store_true", default=True)

    # status
    sub.add_parser("status", help="Lint status")

    # details
    p_det = sub.add_parser("details", help="Get run details")
    p_det.add_argument("--run-id", required=True)
    p_det.add_argument("--severity", choices=["blocking", "warning", "informational"])
    p_det.add_argument("--max-issues", type=int, default=10)

    args = parser.parse_args()
    {"run": cmd_run, "fix": cmd_fix, "status": cmd_status, "details": cmd_details}[args.command](args)


if __name__ == "__main__":
    main()
