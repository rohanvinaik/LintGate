"""Lint tools — lint_files, lint_project, lint_get_details, lint_status, audit_tool_versions, lint_fix."""

from __future__ import annotations

import contextlib
import json
import os
import shlex
from pathlib import Path
from typing import Any, Literal


def _tool_package_name(tool: str) -> str:
    """Map external tool executable names to install package names."""
    return "pip-audit" if tool == "pip-audit" else tool


def _project_venv_python(project_root: str) -> str | None:
    """Return project venv python path if present."""
    for venv_name in (".venv", "venv", "env"):
        py = Path(project_root) / venv_name / "bin" / "python"
        if py.exists() and py.is_file():
            return str(py)
    return None


def _format_cmd(cmd: list[str]) -> str:
    """Render command as shell-safe string."""
    return " ".join(shlex.quote(part) for part in cmd)


def _missing_tool_hints(
    project_root: str,
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build actionable install hints for unavailable external tools."""
    missing: dict[str, dict[str, Any]] = {}
    for linter_name, linter in sorted(registry.items()):
        tool = getattr(linter, "required_tool", None)
        if not tool:
            continue
        if _linter_available(linter, project_root):
            continue

        entry = missing.setdefault(
            tool,
            {
                "tool": tool,
                "package": _tool_package_name(tool),
                "required_by": [],
                "reason": "executable_not_found",
            },
        )
        entry["required_by"].append(linter_name)

    venv_python = _project_venv_python(project_root)
    hints: list[dict[str, Any]] = []
    for tool in sorted(missing):
        item = missing[tool]
        package = item["package"]
        if venv_python:
            cmd = [venv_python, "-m", "pip", "install", package]
            install_command = _format_cmd(cmd)
            auto_installable = tool in {"ty", "pip-audit"}
        else:
            install_command = f"pip install {package}"
            auto_installable = False

        hints.append(
            {
                **item,
                "install_command": install_command,
                "auto_installable": auto_installable,
            }
        )
    return hints


def _linter_available(linter: Any, project_root: str) -> bool:
    """Check linter availability with backward-compatible call signatures."""
    try:
        return bool(linter.available(project_root=project_root))
    except TypeError:
        return bool(linter.available())


from mcp_tools._disk_helpers import tool_response

def register(mcp, helpers):
    """Register lint tools on the shared MCP instance."""

    @mcp.tool()
    def lint_files(
        files: list[str],
        tier: int = 2,
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
        if tier not in (0, 1, 2, 3):
            raise ValueError(f"Invalid tier {tier}; expected one of: 0, 1, 2, 3")
        if not files:
            raise ValueError("No files specified")

        resolved_project_root = (
            helpers["_validate_project_root"](project_root, arg_name="project_root")
            if project_root
            else os.path.dirname(os.path.abspath(files[0]))
        )
        existing, missing = helpers["_resolve_files"](files, resolved_project_root)

        if not existing:
            raise ValueError(f"No specified files exist. Missing: {missing}")

        result = helpers["_run_lint"](
            existing,
            resolved_project_root,
            int(tier),
            strictness,
            output_mode="compact",
        )
        if missing:
            result["missing_files"] = missing

        # Refactor state integration (#199): auto-update per-file findings
        try:
            from lintgate.refactor_state import update_file_findings

            issue_count = result.get("issue_count", 0)
            for f in existing:
                rel = os.path.relpath(f, resolved_project_root)
                update_file_findings(resolved_project_root, rel, issue_count)
        except Exception:
            pass

        blocking = result.get("blocking_count", 0)
        issues = result.get("issue_count", 0)
        summary = f"{issues} issues found in {len(existing)} files. {blocking} blocking."
        return tool_response(
            result, "lint_files", resolved_project_root, summary,
            run_id=result.get("run_id", ""), next_actions=result.get("next_actions"),
        )

    @mcp.tool()
    def lint_project(
        path: str,
        tier: int = 2,
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
        if tier not in (0, 1, 2, 3):
            raise ValueError(f"Invalid tier {tier}; expected one of: 0, 1, 2, 3")
        project_root = helpers["_validate_project_root"](path)

        py_files = helpers["_collect_python_files"](project_root)
        if not py_files:
            raise ValueError(f"No Python files found under: {project_root}")

        result = helpers["_run_lint"](
            py_files,
            project_root,
            int(tier),
            strictness,
            output_mode="compact",
        )
        result["total_python_files"] = len(py_files)
        blocking = result.get("blocking_count", 0)
        issues = result.get("issue_count", 0)
        summary = f"{issues} issues across {len(py_files)} files. {blocking} blocking."
        return tool_response(
            result, "lint_project", project_root, summary,
            run_id=result.get("run_id", ""), next_actions=result.get("next_actions"),
        )

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
        from lintgate.state import load_run_details

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

        sev_label = severity or "all"
        summary = f"Details for run {run_id}: {output['total_matching']} issues ({sev_label} severity)."
        return tool_response(output, "lint_get_details", os.getcwd(), summary, run_id=run_id)

    @mcp.tool()
    def lint_status(path: str | None = None) -> str:
        """Show LintGate status: linters, run history, context, version audits, and today's metrics."""
        from lintgate.config import load_config
        from lintgate.context_guidance import (
            build_context_guidance,
            summarize_context_guidance,
        )
        from lintgate.registry import build_registry
        from lintgate.state import METRICS_DIR, load_last_run, load_last_version_audit
        from lintgate.versioning import format_version_audit_summary

        project_root = helpers["_validate_project_root"](path) if path else os.getcwd()

        status: dict[str, Any] = {
            "version": "0.2.0",
        }

        config = load_config(project_root)
        registry = build_registry(config)
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
        _onboarding = helpers["_build_onboarding_status"](project_root)
        if _onboarding.get("config_state") != "config_enabled":
            status["onboarding"] = _onboarding

        missing = len(status.get("missing_tools", []))
        linter_count = status.get("linter_count", 0)
        last = status.get("last_run")
        last_info = f"run_id={last.get('run_id', '?')}" if last else "none"
        summary = f"LintGate v{status.get('version', '?')}: {linter_count} linters, {missing} missing tools. Last run: {last_info}."
        return tool_response(status, "lint_status", project_root, summary)

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
        from lintgate.config import load_config
        from lintgate.state import log_version_event, save_version_audit
        from lintgate.versioning import format_version_audit_summary, run_version_audit

        project_root = helpers["_validate_project_root"](path)
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

        result = {"summary": summary, **audit}
        issue_count = summary.get("issue_count", 0) if isinstance(summary, dict) else 0
        fix_info = f" auto_fix={auto_fix}" if auto_fix else ""
        sum_text = f"Version audit: {issue_count} issues found.{fix_info}"
        return tool_response(result, "audit_tool_versions", project_root, sum_text)

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
            project_root = helpers["_validate_project_root"](path)
        else:
            project_root = os.path.dirname(os.path.abspath(files[0]))  # type: ignore[index]

        # Resolve files
        if files:
            existing, _missing = helpers["_resolve_files"](files, project_root)
            target_files = existing
        else:
            target_files = helpers["_collect_python_files"](project_root)

        if not target_files:
            return json.dumps({"error": "No Python files found", "dry_run": dry_run})

        result = run_safe_fixes(
            files=target_files,
            project_root=project_root,
            dry_run=dry_run,
            safe_only=safe_only,
        )

        rd = result.to_dict()
        fixed = rd.get("fixed_count", 0)
        summary = f"{fixed} fixes applied (dry_run={dry_run})."
        return tool_response(rd, "lint_fix", project_root, summary)

    return {
        "lint_files": lint_files,
        "lint_project": lint_project,
        "lint_get_details": lint_get_details,
        "lint_status": lint_status,
        "audit_tool_versions": audit_tool_versions,
        "lint_fix": lint_fix,
    }
